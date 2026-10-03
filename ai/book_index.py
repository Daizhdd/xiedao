"""Derived chapter search index; source edits stay authoritative via triggers."""
import re
import sqlite3


HEADERS = 'c.id,c.volume,c.chapter_no,c.title,c.chapter_card,c.summary'


def ensure_index(db):
    ready = getattr(db, '_chapter_search_ready', None)
    if ready is not None:
        return ready
    if db.conn.in_transaction:
        return False
    db.conn.execute('SAVEPOINT chapter_search_setup')
    try:
        existed = db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='chapter_search'").fetchone()
        db.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chapter_search USING fts5("
                        "title,chapter_card,summary,content,content='chapters',"
                        "content_rowid='id',tokenize='trigram')")
        db.conn.execute('''CREATE TRIGGER IF NOT EXISTS chapter_search_insert
            AFTER INSERT ON chapters BEGIN
            INSERT INTO chapter_search(rowid,title,chapter_card,summary,content)
            VALUES(new.id,new.title,new.chapter_card,new.summary,new.content); END''')
        db.conn.execute('''CREATE TRIGGER IF NOT EXISTS chapter_search_delete
            AFTER DELETE ON chapters BEGIN
            INSERT INTO chapter_search(chapter_search,rowid,title,chapter_card,summary,content)
            VALUES('delete',old.id,old.title,old.chapter_card,old.summary,old.content); END''')
        db.conn.execute('''CREATE TRIGGER IF NOT EXISTS chapter_search_update
            AFTER UPDATE OF title,chapter_card,summary,content ON chapters
            WHEN new.title IS NOT old.title OR new.chapter_card IS NOT old.chapter_card
              OR new.summary IS NOT old.summary OR new.content IS NOT old.content
            BEGIN
            INSERT INTO chapter_search(chapter_search,rowid,title,chapter_card,summary,content)
            VALUES('delete',old.id,old.title,old.chapter_card,old.summary,old.content);
            INSERT INTO chapter_search(rowid,title,chapter_card,summary,content)
            VALUES(new.id,new.title,new.chapter_card,new.summary,new.content); END''')
        if not existed:
            db.conn.execute("INSERT INTO chapter_search(chapter_search) VALUES('rebuild')")
        db.conn.execute('RELEASE chapter_search_setup')
        db._chapter_search_ready = True
    except sqlite3.OperationalError:
        db.conn.execute('ROLLBACK TO chapter_search_setup')
        db.conn.execute('RELEASE chapter_search_setup')
        db._chapter_search_ready = False
    return db._chapter_search_ready


def trigram_terms(question):
    stop = set('的了吗呢吧么谁哪怎如何为啥多少几是也就还在有这那与和及或把被会要')
    terms = []
    for chunk in re.findall(r'[\w\u4e00-\u9fff]+', question or ''):
        for index in range(len(chunk) - 2):
            term = chunk[index:index + 3]
            if not any(char in stop for char in term) and term not in terms:
                terms.append(term)
            if len(terms) >= 24:
                return terms
    return terms


def search(db, project_id, question, keywords, limit=3):
    """Return bounded metadata; only final selected chapters load their bodies."""
    terms = trigram_terms(question)
    if terms and ensure_index(db):
        query = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
        try:
            rows = db.conn.execute(
                f'SELECT {HEADERS} FROM chapter_search JOIN chapters c ON c.id=chapter_search.rowid '
                'WHERE chapter_search MATCH ? AND c.project_id=? '
                'ORDER BY bm25(chapter_search,5.0,3.0,3.0,1.0),c.id LIMIT ?',
                (query, project_id, limit)).fetchall()
            if rows:
                return rows, 'indexed'
        except sqlite3.OperationalError:
            pass
    if not keywords:
        return [], 'none'
    keywords = keywords[:24]
    expression = '+'.join(
        '(5*(instr(c.title,?)>0)+3*(instr(c.chapter_card,?)>0)+'
        '3*(instr(c.summary,?)>0)+(instr(c.content,?)>0))' for _ in keywords)
    values = [term for term in keywords for _ in range(4)]
    rows = db.conn.execute(
        f'SELECT {HEADERS},({expression}) AS relevance FROM chapters c '
        'WHERE c.project_id=? AND relevance>0 ORDER BY relevance DESC,c.id LIMIT ?',
        (*values, project_id, limit)).fetchall()
    return rows, 'fallback'


def excerpts(content, question, keywords, max_chars=1000):
    """Prefer a matched passage, including matches in the middle of long text."""
    terms = trigram_terms(question) + list(keywords)
    positions = []
    for term in terms:
        position = content.find(term)
        if position >= 0 and position not in positions:
            positions.append(position)
    if not positions:
        return []
    result, remaining = [], max_chars
    for position in positions:
        if any(match['start'] <= position < match['start'] + len(match['text']) for match in result) or remaining < 100:
            continue
        start = max(0, position - 140)
        end = min(len(content), start + min(600, remaining))
        result.append({'start': start, 'text': content[start:end]})
        remaining -= end - start
        if len(result) >= 2:
            break
    return sorted(result, key=lambda match: match['start'])
