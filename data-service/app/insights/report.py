"""Render the insights payload as an e-mail-safe HTML page (inline styles, tables) and
store it in insight_reports. Optionally adds a short LLM-written executive summary."""
from __future__ import annotations

import json
import logging

from jinja2 import Environment, select_autoescape

from app.ai.llm import LLMError, get_llm
from app.config import get_settings
from app.db import connect
from app.insights import engine, mailer

log = logging.getLogger(__name__)

TEMPLATE = """<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f4f1;font-family:Segoe UI,Roboto,Arial,sans-serif;color:#0b0b0b">
<table role="presentation" width="100%" style="max-width:680px;margin:0 auto;background:#fcfcfb;border:1px solid #e3e2dd;border-radius:12px">
<tr><td style="padding:24px 28px 8px">
  <div style="font-size:12px;color:#52514e;text-transform:uppercase;letter-spacing:.06em">Daily Business Intelligence Report</div>
  <h1 style="font-size:20px;margin:6px 0 4px">{{ p.headline }}</h1>
  <div style="font-size:13px;color:#7a7974">Data through {{ p.as_of }} · week {{ p.period.current_start }} to {{ p.period.current_end }} vs previous 7 days</div>
</td></tr>
{% if summary %}<tr><td style="padding:8px 28px"><p style="font-size:14px;line-height:1.55;margin:8px 0;background:#f0efec;padding:12px 14px;border-radius:8px">{{ summary }}</p></td></tr>{% endif %}
<tr><td style="padding:8px 28px">
  <table role="presentation" width="100%" style="border-collapse:collapse;font-size:13px">
  {% for k in p.kpis %}
    <tr><td style="padding:8px 0;border-bottom:1px solid #e8e7e2;color:#52514e">{{ k.label }}</td>
        <td style="padding:8px 0;border-bottom:1px solid #e8e7e2;text-align:right;font-weight:600">{{ money(k.value) if k.money else num(k.value) }}</td>
        <td style="padding:8px 0 8px 12px;border-bottom:1px solid #e8e7e2;text-align:right;width:90px;color:{{ '#006300' if k.good else ('#b42323' if k.good == False else '#7a7974') }}">
          {% if k.change_pct is not none %}{{ '▲' if k.change_pct >= 0 else '▼' }} {{ '%.1f'|format(k.change_pct|abs) }}%{% else %}–{% endif %}</td></tr>
  {% endfor %}
  </table>
</td></tr>
<tr><td style="padding:16px 28px 4px"><h2 style="font-size:15px;margin:0 0 8px">Highlights</h2>
  <ul style="margin:0;padding-left:18px;font-size:14px;line-height:1.6">{% for b in p.bullets %}<li>{{ b }}</li>{% endfor %}</ul></td></tr>
{% if p.anomalies %}<tr><td style="padding:16px 28px 4px"><h2 style="font-size:15px;margin:0 0 8px">Anomalies (vs 8-week baseline)</h2>
  <table role="presentation" width="100%" style="border-collapse:collapse;font-size:13px">
  <tr style="color:#52514e;text-align:left"><th style="padding:6px 0">Segment</th><th>This week</th><th>8-wk avg</th><th style="text-align:right">Change</th><th style="text-align:right">z</th></tr>
  {% for a in p.anomalies[:8] %}<tr>
    <td style="padding:6px 0;border-top:1px solid #e8e7e2">{{ a.member }} <span style="color:#7a7974">({{ a.dimension }})</span></td>
    <td style="border-top:1px solid #e8e7e2">{{ money(a.current) }}</td><td style="border-top:1px solid #e8e7e2">{{ money(a.baseline_mean) }}</td>
    <td style="border-top:1px solid #e8e7e2;text-align:right;color:{{ '#006300' if a.change_pct > 0 else '#b42323' }}">{{ '%+.0f'|format(a.change_pct) }}%</td>
    <td style="border-top:1px solid #e8e7e2;text-align:right">{{ '%+.1f'|format(a.z) }}</td></tr>{% endfor %}
  </table></td></tr>{% endif %}
<tr><td style="padding:16px 28px 4px"><h2 style="font-size:15px;margin:0 0 8px">Support SLA</h2>
  <p style="font-size:14px;margin:0 0 6px">{{ p.sla.breached_7d }} breaches out of {{ p.sla.resolved_7d }} resolved tickets{% if p.sla.breach_rate_pct is not none %} ({{ p.sla.breach_rate_pct }}%){% endif %}.
  {{ p.sla.open_overdue }} open tickets are past SLA, and {{ p.sla.open_high_priority }} open tickets are HIGH or CRITICAL priority.</p>
  {% if p.sla.by_category %}<table role="presentation" width="100%" style="border-collapse:collapse;font-size:13px">
  {% for c in p.sla.by_category %}<tr><td style="padding:4px 0;border-top:1px solid #e8e7e2">{{ c.category }}</td>
    <td style="border-top:1px solid #e8e7e2;text-align:right">{{ c.breached }} / {{ c.resolved }} breached</td></tr>{% endfor %}</table>{% endif %}
</td></tr>
<tr><td style="padding:20px 28px 24px;font-size:12px;color:#7a7974">Open the live dashboard: <a href="{{ app_url }}" style="color:#1c5cab">{{ app_url }}</a><br>
Generated automatically by the Enterprise Analytics Platform insights job. All data is synthetic.</td></tr>
</table></body></html>"""

_env = Environment(autoescape=select_autoescape(default=True))


def _money(v):
    return "–" if v is None else engine._inr(float(v))


def _num(v):
    return "–" if v is None else f"{v:,}"


def render(payload: dict, summary: str | None) -> str:
    return _env.from_string(TEMPLATE).render(p=payload, summary=summary, money=_money, num=_num,
                                             app_url=get_settings().public_app_url)


def executive_summary(payload: dict) -> str | None:
    llm = get_llm()
    if llm is None:
        return None
    prompt = ("Write a 2-3 sentence executive summary for a management e-mail from these facts. "
              "Be specific with numbers, neutral in tone, no bullet points, no greeting.\n\n" + "\n".join(payload["bullets"]))
    try:
        return llm.chat([{"role": "user", "content": prompt}], temperature=0.3).strip()
    except LLMError as exc:
        log.warning("summary generation failed: %s", exc)
        return None


def generate(trigger: str = "manual", send_email: bool = False, as_of=None) -> dict:
    payload = engine.compute(as_of)
    summary = executive_summary(payload)
    html = render(payload, summary)
    email_status, recipients = None, None
    if send_email:
        recipients, email_status = mailer.send(f"Daily BI report: {payload['headline'][:80]}", html, payload)
    with connect() as conn:
        report_id = conn.execute(
            """INSERT INTO insight_reports (as_of_date, trigger, headline, summary, payload, html, emailed_to, email_status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (payload["as_of"], trigger, payload["headline"], summary, json.dumps(payload, default=str), html,
             recipients, email_status)).fetchone()["id"]
        conn.commit()
    return {"id": report_id, "as_of": payload["as_of"], "headline": payload["headline"], "summary": summary,
            "bullets": payload["bullets"], "anomalies": len(payload["anomalies"]), "email_status": email_status}
