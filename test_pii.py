from app import sanitize_text_for_ai

cases = [
    ("my zip is 28535", "my zip is [ZIP]"),
    ("zip code: 28535", "[ZIP]"),
    ("postal code 28535", "postal code [ZIP]"),
    ("28535-1234", "[ZIP]"),
    ("the year 1984", "the year 1984"),
    ("Orwell's 1984", "Orwell's 1984"),
    ("5000 words", "5000 words"),
    ("2001: A Space Odyssey", "2001: A Space Odyssey"),
    ("he was born in 2010", "he was born in 2010"),
    ("call me at 555-123-4567", "call me at [PHONE]"),
    ("email me at a@b.com", "email me at [EMAIL]"),
    ("I live at 101 Main St", "I live at [ADDRESS]"),
    ("@johndoe", "[HANDLE]"),
    ("my ssn is 123-45-6789", "my ssn is [SSN]"),
    ("student ID: student-12345", "student ID: [STUDENT_ID]"),
]

fail = 0
for inp, expected in cases:
    got = sanitize_text_for_ai(inp)
    ok = (got == expected)
    print(f"{'✅' if ok else '❌'}  {inp!r:45} → {got!r}")
    if not ok:
        print(f"     expected: {expected!r}")
        fail += 1

print(f"\n{len(cases) - fail}/{len(cases)} passed")