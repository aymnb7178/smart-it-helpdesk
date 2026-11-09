"""SYNTHETIC ticket corpus generator.

Everything produced here is artificial: tickets are assembled from hand-written
templates with random slot values, optional filler sentences, priority cue phrases,
occasional typos and deliberate label noise. No real tickets or personal data are used.
Because the data is templated, accuracy measured on it is much easier than real life.
"""

from __future__ import annotations

import random
from typing import Iterator

from helpdesk.config import CATEGORIES, PRIORITIES

SLOTS = {
    "device": ["laptop", "desktop", "monitor", "docking station", "printer", "keyboard",
               "mouse", "headset", "projector", "scanner", "tablet"],
    "app": ["Outlook", "Teams", "Excel", "Word", "Chrome", "Zoom", "Photoshop", "the ERP system",
            "the VPN client", "Slack", "the accounting software", "Visual Studio", "Adobe Reader"],
    "loc": ["the 2nd floor", "reception", "the finance department", "meeting room B", "the HR office",
            "the warehouse", "the server room", "my home office", "the branch office"],
    "err": ["0x80070005", "E-1042", "500", "0xC000007B", "code 17", "ERR_CONNECTION_RESET"],
}

TEMPLATES: dict[str, list[str]] = {
    "hardware": [
        "My {device} won't turn on this morning",
        "The {device} in {loc} is making a loud clicking noise",
        "{device} screen is flickering and sometimes goes black",
        "Need a replacement {device}, the old one is broken",
        "The battery on my laptop drains in under an hour",
        "{device} keeps overheating and shutting down",
        "Spilled coffee on my {device} and now it does not respond",
        "The {device} is not detected when I plug it in",
    ],
    "software": [
        "{app} crashes every time I open a large file",
        "Please install {app} on my {device}",
        "{app} shows error {err} on startup",
        "After the latest update {app} is very slow",
        "I cannot activate my {app} license",
        "{app} freezes when I try to save",
        "Need to upgrade {app} to the latest version",
        "{app} will not launch after reinstalling",
    ],
    "network": [
        "The wifi in {loc} keeps disconnecting",
        "Internet is very slow on my {device}",
        "Cannot connect to the VPN from home",
        "No network connection in {loc}, the cable seems fine",
        "The shared drive is unreachable, mapped drive shows a red X",
        "Cannot reach the internal website, DNS error {err}",
        "The ethernet port in {loc} is dead",
        "Video calls lag and drop because of packet loss",
    ],
    "account_access": [
        "I forgot my password and need a reset",
        "My account is locked after too many login attempts",
        "Please grant me access to the shared folder of {loc}",
        "New employee starting Monday needs an account created",
        "Multi-factor authentication code is not accepted",
        "Cannot log in to {app}, it says invalid credentials",
        "Need my permissions removed, I changed teams",
        "Password expired and the reset link does not work",
    ],
    "email": [
        "Not receiving emails in Outlook since this morning",
        "My emails are going to the recipients' spam folder",
        "Mailbox is full and I cannot send messages",
        "Need a shared mailbox for {loc}",
        "Calendar invites are not syncing to my phone",
        "Outlook keeps asking for my password again and again",
        "Cannot add an email signature, the option is greyed out",
        "Out of office auto reply is not working",
    ],
    "security": [
        "I received a suspicious email asking for my bank details",
        "Antivirus detected malware on my {device}",
        "A colleague in {loc} may have clicked a phishing link",
        "Lost my company {device}, please wipe it remotely",
        "Unknown login attempts from another country on my account",
        "A ransom message appeared on my {device}",
        "Need a security review for a new USB device policy",
        "Someone shared a strange attachment, is it safe to open?",
    ],
}

FILLERS = [
    "I already restarted it.", "It started yesterday afternoon.", "Other colleagues seem fine.",
    "I have attached nothing yet, can send a screenshot.", "This is the second time this month.",
    "I tried the usual steps from the wiki.", "", "", "",
]

PRIORITY_CUES: dict[str, list[str]] = {
    "critical": ["URGENT: everything is down.", "Nobody in the office can work.", "Critical outage, whole team blocked.",
                 "Production is affected right now.", "Emergency, customers cannot be served."],
    "high": ["Need this fixed today please.", "It is blocking my work.", "Important client meeting soon.",
             "High priority for my deadline.", "I cannot do my job without it."],
    "medium": ["It happens a few times a day.", "It slows me down.", "Please look at it this week.",
               "Somewhat annoying but I can work around it."],
    "low": ["No rush at all.", "Whenever you have time.", "Minor annoyance.", "Nice to have, not urgent.",
            "Low priority, just reporting."],
}
PRIORITY_WEIGHTS = {"critical": 0.08, "high": 0.22, "medium": 0.45, "low": 0.25}


def _fill(template: str, rng: random.Random) -> str:
    out = template
    for key, values in SLOTS.items():
        while "{" + key + "}" in out:
            out = out.replace("{" + key + "}", rng.choice(values), 1)
    return out


def _typo(text: str, rng: random.Random, rate: float = 0.03) -> str:
    words = text.split(" ")
    for i, w in enumerate(words):
        if len(w) > 5 and rng.random() < rate:
            j = rng.randrange(1, len(w) - 1)
            words[i] = w[:j] + w[j + 1:]
    return " ".join(words)


def generate_ticket(rng: random.Random, category_noise: float = 0.04, cue_noise: float = 0.2,
                    no_cue: float = 0.1) -> dict:
    """Return one synthetic ticket: title, description, labels and the template id used.

    ``category_noise``: probability that the text is drawn from a different category than the label.
    ``cue_noise``: probability that the priority cue comes from a random other priority.
    ``no_cue``: probability that no priority cue is included (label is then unpredictable).
    """
    category = rng.choice(CATEGORIES)
    text_cat = category
    if rng.random() < category_noise:
        text_cat = rng.choice([c for c in CATEGORIES if c != category])
    tid = rng.randrange(len(TEMPLATES[text_cat]))
    title = _fill(TEMPLATES[text_cat][tid], rng)
    priority = rng.choices(PRIORITIES, weights=[PRIORITY_WEIGHTS[p] for p in PRIORITIES])[0]
    cue_pool = priority
    if rng.random() < cue_noise:
        cue_pool = rng.choice(PRIORITIES)
    cue = "" if rng.random() < no_cue else rng.choice(PRIORITY_CUES[cue_pool])
    parts = [cue, _fill(rng.choice(FILLERS), rng)]
    rng.shuffle(parts)
    description = _typo(" ".join(p for p in parts if p), rng)
    if rng.random() < 0.2:
        title = title.lower()
    return {
        "title": _typo(title, rng),
        "description": description,
        "category": category,
        "priority": priority,
        "template_id": f"{text_cat}:{tid}",
    }


def generate_tickets(n: int, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    return [generate_ticket(rng) for _ in range(n)]


def iter_texts(records: list[dict]) -> Iterator[str]:
    for r in records:
        yield f"{r['title']}. {r['description']}".strip()
