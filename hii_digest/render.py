"""Render a :class:`~hii_digest.digest.Digest` as inline-styled HTML and plain text."""

from __future__ import annotations

from html import escape

from hii_digest.digest import Digest, DigestItem

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
CATEGORY_COLOURS = {
    "primary": ("#e8f0fe", "#1a56db"),
    "updates": ("#fef3c7", "#92400e"),
    "forums": ("#ede9fe", "#5b21b6"),
    "social": ("#dcfce7", "#166534"),
    "promotions": ("#fee2e2", "#991b1b"),
}


def _fmt_score(score: float) -> str:
    return f"{score:g}"


def _reasons_text(item: DigestItem) -> str:
    return " · ".join(str(r) for r in item.scored.reasons)


def subtitle(digest: Digest) -> str:
    total = digest.stats.total
    return (
        f"{digest.window.label} · {total} email{'s' if total != 1 else ''} since midnight "
        f"({digest.window.tz.key})"
    )


def _item_html(item: DigestItem) -> str:
    e = item.email
    bg, fg = CATEGORY_COLOURS.get(item.scored.category, ("#f3f4f6", "#374151"))
    subject = escape(e.subject or "(no subject)")
    sender = escape(e.sender_display)
    addr = escape(e.from_addr)
    link = escape(item.link, quote=True)
    return f"""
<tr><td style="padding:16px 0;border-bottom:1px solid #eceef1;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td width="40" valign="top" style="padding-right:12px;">
      <div style="width:30px;height:30px;border-radius:15px;background:#1f2a44;color:#ffffff;text-align:center;font-family:{FONT};font-size:13px;font-weight:600;line-height:30px;">{item.rank}</div>
    </td>
    <td valign="top" style="font-family:{FONT};">
      <div style="font-size:13px;color:#4b5563;margin-bottom:3px;">
        <strong style="color:#111827;">{sender}</strong>
        <span style="color:#9ca3af;">&lt;{addr}&gt;</span> · {escape(item.time_label)}
        <span style="display:inline-block;margin-left:6px;padding:1px 8px;border-radius:9px;background:{bg};color:{fg};font-size:11px;">{escape(item.scored.category)}</span>
      </div>
      <div style="font-size:16px;font-weight:600;margin-bottom:4px;">
        <a href="{link}" style="color:#1a56db;text-decoration:none;">{subject}</a>
      </div>
      <div style="font-size:14px;line-height:1.45;color:#1f2937;margin-bottom:6px;">{escape(item.summary)}</div>
      <div style="font-size:11px;line-height:1.4;color:#6b7280;">
        <strong>score {_fmt_score(item.scored.score)}</strong> · {escape(_reasons_text(item))}
        · <a href="{link}" style="color:#6b7280;">Open in Gmail</a>
      </div>
    </td>
  </tr></table>
</td></tr>"""


def render_html(digest: Digest) -> str:
    items_html = "".join(_item_html(i) for i in digest.items)
    if not digest.items:
        items_html = (
            f'<tr><td style="padding:24px 0;font:14px {FONT};color:#4b5563;">'
            "Nothing important has arrived since midnight.</td></tr>"
        )
    notes_html = "".join(
        f'<p style="margin:12px 0 0;font:13px {FONT};color:#92400e;">{escape(n)}</p>' for n in digest.notes
    )
    source = "AI summary" if digest.summary_source == "llm" else "Summary"
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(digest.title)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5f7;"><tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
  <tr><td style="background:#1f2a44;padding:22px 28px;font-family:{FONT};">
    <div style="font-size:12px;letter-spacing:1.5px;text-transform:uppercase;color:#a5b4fc;">hii digest</div>
    <div style="font-size:24px;font-weight:700;color:#ffffff;margin-top:4px;">{escape(digest.title)}</div>
    <div style="font-size:13px;color:#cbd5e1;margin-top:4px;">{escape(subtitle(digest))}</div>
  </td></tr>
  <tr><td style="padding:20px 28px 4px;">
    <div style="background:#f0f4ff;border-left:4px solid #4f46e5;border-radius:6px;padding:12px 16px;font-family:{FONT};">
      <div style="font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#4338ca;margin-bottom:4px;">{source} of your day</div>
      <div style="font-size:14px;line-height:1.5;color:#1f2937;">{escape(digest.overall)}</div>
    </div>
    {notes_html}
  </td></tr>
  <tr><td style="padding:4px 28px 8px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{items_html}</table>
  </td></tr>
  <tr><td style="padding:14px 28px 22px;font:11px/1.5 {FONT};color:#9ca3af;">
    Ranked by transparent points: starred, Gmail important, unread, Primary tab, real person, sent to you,
    active thread, your replies and urgent keywords score up; promotions, social, forums, updates and
    bulk senders score down. Reply <strong>{escape(digest.trigger_word)}</strong> any time for a fresh digest.
  </td></tr>
</table>
</td></tr></table>
</body></html>
"""


def render_text(digest: Digest) -> str:
    lines = [digest.title.upper(), subtitle(digest), ""]
    lines += ["Summary of your day:", digest.overall, ""]
    lines += digest.notes + ([""] if digest.notes else [])
    if not digest.items:
        lines.append("Nothing important has arrived since midnight.")
    for item in digest.items:
        e = item.email
        lines.append(f"{item.rank}. {e.subject or '(no subject)'}")
        lines.append(
            f"   From: {e.sender_display} <{e.from_addr}> · {item.time_label} · {item.scored.category}"
        )
        lines.append(f"   {item.summary}")
        lines.append(f"   Score {_fmt_score(item.scored.score)}: {_reasons_text(item)}")
        lines.append(f"   {item.link}")
        lines.append("")
    lines.append(f"Reply '{digest.trigger_word}' any time for a fresh digest. (sent by hii-digest)")
    return "\n".join(lines).rstrip() + "\n"
