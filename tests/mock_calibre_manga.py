import os
import sqlite3

def create_mock_calibre_manga_db(db_path: str):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            sort TEXT,
            author_sort TEXT,
            series_index REAL DEFAULT 1.0,
            path TEXT DEFAULT '',
            has_cover INTEGER DEFAULT 0
        );

        CREATE TABLE series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            sort TEXT
        );

        CREATE TABLE books_series_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            series INTEGER NOT NULL
        );

        CREATE TABLE authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            sort TEXT,
            link TEXT DEFAULT ''
        );

        CREATE TABLE books_authors_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            author INTEGER NOT NULL
        );

        CREATE TABLE publishers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            sort TEXT
        );

        CREATE TABLE books_publishers_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            publisher INTEGER NOT NULL
        );

        CREATE TABLE tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );

        CREATE TABLE books_tags_link (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            tag INTEGER NOT NULL
        );
    """)

    # Seed Publishers
    cur.execute("INSERT INTO publishers (id, name) VALUES (1, 'VIZ Media');")
    cur.execute("INSERT INTO publishers (id, name) VALUES (2, 'Kodansha');")

    # Seed Tags
    cur.execute("INSERT INTO tags (id, name) VALUES (1, 'Action');")
    cur.execute("INSERT INTO tags (id, name) VALUES (2, 'Shounen');")
    cur.execute("INSERT INTO tags (id, name) VALUES (3, 'Supernatural');")

    # Seed Series 1: Chainsaw Man (Owned Vol 1, 2, 3)
    cur.execute("INSERT INTO series (id, name) VALUES (1, 'Chainsaw Man');")
    cur.execute("INSERT INTO authors (id, name) VALUES (1, 'Tatsuki Fujimoto');")

    for vol in [1.0, 2.0, 3.0]:
        cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"Chainsaw Man Vol. {int(vol)}", vol))
        b_id = cur.lastrowid
        cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, 1);", (b_id,))
        cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, 1);", (b_id,))
        cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, 1);", (b_id,))
        cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (?, 1);", (b_id,))
        cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (?, 2);", (b_id,))
        cur.execute("INSERT INTO books_tags_link (book, tag) VALUES (?, 3);", (b_id,))

    # Seed Series 2: Frieren: Beyond Journey's End (Owned Vol 1, 2, 3)
    cur.execute("INSERT INTO series (id, name) VALUES (2, ?);", ("Frieren: Beyond Journey's End",))
    cur.execute("INSERT INTO authors (id, name) VALUES (2, ?);", ("Kanehito Yamada",))

    for vol in [1.0, 2.0, 3.0]:
        cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"Frieren Vol. {int(vol)}", vol))
        b_id = cur.lastrowid
        cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, 2);", (b_id,))
        cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, 2);", (b_id,))
        cur.execute("INSERT INTO books_publishers_link (book, publisher) VALUES (?, 1);", (b_id,))

    # Seed Series 3: One Piece (Owned Vol 1 to 104)
    cur.execute("INSERT INTO series (id, name) VALUES (3, 'One Piece');")
    cur.execute("INSERT INTO authors (id, name) VALUES (3, 'Eiichiro Oda');")

    for vol in range(1, 105):
        cur.execute("INSERT INTO books (title, series_index) VALUES (?, ?);", (f"One Piece Vol. {vol}", float(vol)))
        b_id = cur.lastrowid
        cur.execute("INSERT INTO books_series_link (book, series) VALUES (?, 3);", (b_id,))
        cur.execute("INSERT INTO books_authors_link (book, author) VALUES (?, 3);", (b_id,))

    conn.commit()
    conn.close()
    return db_path
