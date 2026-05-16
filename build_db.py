import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VCF_PATH = os.path.join(BASE_DIR, 'driverange Rancho Saint Joaquin and 111 others.vcf')
DB_PATH  = os.path.join(BASE_DIR, 'contacts.db')


def decode_vcf_value(val):
    # quoted-printable soft line break
    val = re.sub(r'=\r?\n', '', val)
    val = val.replace('\\n', ' ')
    val = val.replace('\\,', ',')
    val = val.replace('\\;', ';')
    val = val.replace('\\\\', '\\')
    return val.strip()


def parse_vcf(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        raw = f.read()

    # unfold RFC 6350 continuation lines
    raw = re.sub(r'\r?\n[ \t]', '', raw)

    contacts = []
    blocks = re.split(r'BEGIN:VCARD', raw, flags=re.IGNORECASE)

    for block in blocks:
        end = re.search(r'END:VCARD', block, re.IGNORECASE)
        if not end:
            continue
        block = block[:end.start()]

        fn = ''
        n_val = ''
        org = ''
        phones = []
        emails = []

        for line in block.splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue

            # split on first colon only
            prop_raw, _, value = line.partition(':')
            prop_raw = prop_raw.upper()
            value = decode_vcf_value(value)

            # strip parameters (e.g. TYPE=VOICE)
            prop_name = prop_raw.split(';')[0]

            if prop_name == 'FN':
                fn = value
            elif prop_name == 'N' and not fn:
                parts = value.split(';')
                last  = parts[0].strip() if len(parts) > 0 else ''
                first = parts[1].strip() if len(parts) > 1 else ''
                fn = f'{first} {last}'.strip()
            elif prop_name == 'ORG':
                org = value.split(';')[0].strip()
            elif prop_name == 'TEL':
                # extract TYPE from prop_raw params
                tel_type = 'VOICE'
                for param in prop_raw.split(';')[1:]:
                    if param.startswith('TYPE='):
                        tel_type = param[5:]
                        break
                if value:
                    phones.append((value, tel_type))
            elif prop_name == 'EMAIL':
                if value:
                    emails.append(value)

        if fn or phones or emails:
            contacts.append({'fn': fn, 'org': org, 'phones': phones, 'emails': emails})

    return contacts


def build_db(contacts, db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript('''
        DROP TABLE IF EXISTS emails;
        DROP TABLE IF EXISTS phones;
        DROP TABLE IF EXISTS contacts;

        CREATE TABLE contacts (
            id  INTEGER PRIMARY KEY AUTOINCREMENT,
            fn  TEXT NOT NULL DEFAULT '',
            org TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE phones (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            number     TEXT NOT NULL,
            type       TEXT NOT NULL DEFAULT 'VOICE'
        );

        CREATE TABLE emails (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            address    TEXT NOT NULL
        );

        CREATE INDEX idx_contacts_fn  ON contacts(fn  COLLATE NOCASE);
        CREATE INDEX idx_contacts_org ON contacts(org COLLATE NOCASE);
        CREATE INDEX idx_phones_number     ON phones(number);
        CREATE INDEX idx_phones_contact_id ON phones(contact_id);
        CREATE INDEX idx_emails_address    ON emails(address COLLATE NOCASE);
        CREATE INDEX idx_emails_contact_id ON emails(contact_id);
    ''')

    for c in contacts:
        cur.execute('INSERT INTO contacts (fn, org) VALUES (?, ?)', (c['fn'], c['org']))
        cid = cur.lastrowid
        cur.executemany('INSERT INTO phones (contact_id, number, type) VALUES (?, ?, ?)',
                        [(cid, p[0], p[1]) for p in c['phones']])
        cur.executemany('INSERT INTO emails (contact_id, address) VALUES (?, ?)',
                        [(cid, e) for e in c['emails']])

    conn.commit()
    conn.close()

    phone_count = sum(len(c['phones']) for c in contacts)
    email_count = sum(len(c['emails']) for c in contacts)
    print(f'Imported {len(contacts)} contacts, {phone_count} phones, {email_count} emails → {db_path}')


if __name__ == '__main__':
    contacts = parse_vcf(VCF_PATH)
    build_db(contacts, DB_PATH)
