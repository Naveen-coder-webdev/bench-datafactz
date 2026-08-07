"""
people.py — builds the synthetic person pool for the breach corpus.

Each person is a dict:
{
  person_id, first, last, full_name, aliases: [...], dob, ssn, dl_number,
  passport_number, email, phone, address, employer, job_title,
  card_number, medical_condition, username, password,
  is_name_collision_with: <person_id or None>  # deliberate shared-name trap
}

Edge cases baked in here (identity-level; document-level edge cases are
added by the planters):
  - Nickname variants (Robert/Bob, etc.)
  - Maiden-name variants for a subset of people
  - Initials-only variants
  - Misspelling variants
  - Deliberate shared-name pairs: two DIFFERENT people, same full name,
    different DOB/SSN/address — entity resolution must NOT merge these.
"""

import random
import json
from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)

NICKNAMES = {
    "Robert": "Bob", "William": "Bill", "Richard": "Rick", "James": "Jim",
    "Elizabeth": "Liz", "Margaret": "Peggy", "Katherine": "Kate",
    "Michael": "Mike", "Christopher": "Chris", "Jennifer": "Jen",
    "Patricia": "Pat", "Charles": "Chuck", "Anthony": "Tony",
    "Deborah": "Debbie", "Susan": "Sue", "Kenneth": "Ken",
    "Timothy": "Tim", "Rebecca": "Becky", "Joseph": "Joe",
    "Barbara": "Barb",
}

MEDICAL_CONDITIONS = [
    "Type 2 Diabetes", "Hypertension", "Asthma", "Major Depressive Disorder",
    "Generalized Anxiety Disorder", "Hypothyroidism", "Migraine",
    "Coronary Artery Disease", "Rheumatoid Arthritis", "GERD",
]

EMPLOYERS = [
    "Meridian Health Partners", "Cascade Financial Group", "Union Retail Co",
    "Brightpath Logistics", "Northgate Manufacturing", "Silverline Insurance",
    "Harborview Medical Center", "Pinecrest School District",
]


def _misspell(name: str) -> str:
    """Swap two adjacent interior characters to simulate a data-entry typo."""
    if len(name) < 4:
        return name
    i = random.randint(1, len(name) - 3)
    chars = list(name)
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def _make_base_person(pid: str, first=None, last=None) -> dict:
    first = first or fake.first_name()
    last = last or fake.last_name()
    dob = fake.date_of_birth(minimum_age=18, maximum_age=85)
    return {
        "person_id": pid,
        "first": first,
        "last": last,
        "full_name": f"{first} {last}",
        "maiden_name": None,
        "aliases": [],
        "dob": dob.isoformat(),
        "ssn": fake.ssn(),
        "dl_number": fake.bothify("D#######"),
        "passport_number": fake.bothify("##########"),
        "email": f"{first.lower()}.{last.lower()}{random.randint(1,99)}@{fake.free_email_domain()}",
        "phone": fake.phone_number(),
        "address": fake.address().replace("\n", ", "),
        "employer": random.choice(EMPLOYERS),
        "job_title": fake.job(),
        "card_number": fake.credit_card_number(card_type="visa"),
        "card_exp": fake.credit_card_expire(),
        "medical_condition": random.choice(MEDICAL_CONDITIONS),
        "username": f"{first.lower()}{last.lower()}{random.randint(10,99)}",
        "password": fake.password(length=10),
        "is_name_collision_with": None,
    }


def _attach_variants(person: dict):
    """Populate aliases: nickname, initials, misspelling, and (sometimes) maiden name."""
    first, last = person["first"], person["last"]
    aliases = set()

    if first in NICKNAMES:
        aliases.add(f"{NICKNAMES[first]} {last}")

    aliases.add(f"{first[0]}. {last}")
    aliases.add(f"{first} {last[0]}.")

    if random.random() < 0.35:
        aliases.add(f"{_misspell(first)} {last}")

    if random.random() < 0.25 and person["first"].lower() not in ("john", "james"):
        maiden = fake.last_name()
        person["maiden_name"] = maiden
        aliases.add(f"{first} {maiden}")

    aliases.discard(person["full_name"])
    person["aliases"] = sorted(aliases)


def build_person_pool(n_people: int = 160, n_shared_name_pairs: int = 8) -> list[dict]:
    people = []
    used_names = []

    for i in range(1, n_people + 1):
        pid = f"P{i:04d}"
        person = _make_base_person(pid)
        _attach_variants(person)
        people.append(person)
        used_names.append((person["first"], person["last"]))

    # Deliberate shared-name traps: pick existing names and mint a SECOND,
    # wholly distinct person under the same full name.
    for j in range(n_shared_name_pairs):
        first, last = random.choice(used_names)
        pid = f"P{n_people + j + 1:04d}"
        twin = _make_base_person(pid, first=first, last=last)
        _attach_variants(twin)
        # tag both directions so the manifest can assert "must not merge"
        twin["is_name_collision_with"] = "TBD"
        people.append(twin)

    # backfill collision references (find the earliest person with same name)
    name_to_first_id = {}
    for p in people:
        key = p["full_name"]
        if key not in name_to_first_id:
            name_to_first_id[key] = p["person_id"]
        elif p["is_name_collision_with"] == "TBD":
            p["is_name_collision_with"] = name_to_first_id[key]

    return people


if __name__ == "__main__":
    pool = build_person_pool()
    with open("output/manifest/people_pool.json", "w") as f:
        json.dump(pool, f, indent=2)
    collisions = [p for p in pool if p["is_name_collision_with"]]
    print(f"Generated {len(pool)} people, {len(collisions)} shared-name collision records.")
