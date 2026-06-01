"""
Ask Aunt Lurlene — a Southern anonymous advice column.

ZERO external dependencies. Uses only the Python standard library
(http.server + sqlite3), so there is NOTHING for pip to install and the build
step that kept failing cannot fail. The entire web page is embedded in this
file and served by the program — there is no static HTML file either.

Storage:
  - By default, a local SQLite file (lurlene.db). Every visitor hitting the
    running site sees the same data, so Lurlene's published answers show up for
    everyone in real time.
  - Optional: if you set DATABASE_URL *and* add psycopg2-binary to
    requirements.txt, it will use PostgreSQL instead (survives redeploys,
    scales across replicas). Not required to run.

Admin login defaults: username "angie" / password "password123"
Override with ADMIN_USER / ADMIN_PASS environment variables.
"""

import os
import json
import random
import secrets
import sqlite3
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADMIN_USER = os.environ.get("ADMIN_USER", "angie")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "password123")
DATABASE_URL = os.environ.get("DATABASE_URL")

# Optional Postgres: only if a DB url is set AND the driver happens to be
# installed. If not, we silently use SQLite. No dependency is required.
PG = None
if DATABASE_URL:
    try:
        import psycopg2 as PG  # noqa: N811
    except Exception:
        PG = None
USE_PG = PG is not None

ANON_NAMES = [
    "Worried Raccoon", "Concerned Biscuit", "Lost in Walmart", "Anonymous Pickle",
    "Troubled in Tampa", "Somebody's Cousin", "Emotionally Damaged Possum",
    "Suspicious Neighbor", "Bewildered Possum", "Overwhelmed Casserole",
    "Nervous Sweet Tea", "Frantic Biscuit", "A Lady with Regrets",
    "Definitely Not Karen", "Confused in Clearwater", "Panicked Peach",
    "A Concerned Citizen", "Distressed Dumpling", "Conflicted Cornbread",
    "Mildly Unhinged Magnolia",
]
REACTION_TYPES = [
    "Bless Your Heart", "Lord Have Mercy", "That's Wild",
    "I Need More Tea", "Pray For Them", "Auntie Was Right",
]

SESSIONS = set()          # active admin session tokens (in memory)
DB_LOCK = threading.Lock()
_conn = None


def random_name():
    return random.choice(ANON_NAMES)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---- database (SQLite by default; Postgres only if available) --------------
def get_conn():
    global _conn
    if _conn is None:
        if USE_PG:
            sslmode = "require" if os.environ.get("PGSSL") == "require" else "disable"
            _conn = PG.connect(DATABASE_URL, sslmode=sslmode)
            _conn.autocommit = True
        else:
            _conn = sqlite3.connect("lurlene.db", check_same_thread=False)
            _conn.row_factory = sqlite3.Row
    return _conn


def _adapt(sql):
    return sql if USE_PG else sql.replace("%s", "?")


def _to_dict(cur, row):
    if row is None:
        return None
    if USE_PG:
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    return {k: row[k] for k in row.keys()}


def q(sql, params=(), fetch=None):
    with DB_LOCK:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        result = None
        if fetch == "one":
            result = _to_dict(cur, cur.fetchone())
        elif fetch == "all":
            result = [_to_dict(cur, r) for r in cur.fetchall()]
        if not USE_PG:
            conn.commit()
        cur.close()
        return result


def insert_id(sql, params=()):
    with DB_LOCK:
        conn = get_conn()
        cur = conn.cursor()
        if USE_PG:
            cur.execute(_adapt(sql) + " RETURNING id", params)
            nid = cur.fetchone()[0]
        else:
            cur.execute(_adapt(sql), params)
            nid = cur.lastrowid
            conn.commit()
        cur.close()
        return nid


def init_db():
    pk = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    q(f"""CREATE TABLE IF NOT EXISTS submissions (
        id {pk}, anon_name TEXT NOT NULL, category TEXT NOT NULL,
        message TEXT NOT NULL, photo_url TEXT,
        status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL)""")
    q(f"""CREATE TABLE IF NOT EXISTS posts (
        id {pk}, submission_id INTEGER, headline TEXT NOT NULL, subheadline TEXT,
        anon_name TEXT NOT NULL, category TEXT NOT NULL, question TEXT NOT NULL,
        answer TEXT NOT NULL, published_at TEXT NOT NULL)""")
    q("""CREATE TABLE IF NOT EXISTS reactions (
        post_id INTEGER NOT NULL, reaction TEXT NOT NULL,
        count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (post_id, reaction))""")
    seed_if_empty()


def seed_if_empty():
    row = q("SELECT COUNT(*) AS n FROM posts", (), "one")
    if row and row["n"] > 0:
        return
    print("[seed] Empty database — planting Aunt Lurlene's first columns...")
    seed_posts = [
        {
            "headline": "Local Woman Reports Her Man Bought Extra-Large Condoms and Nobody in This Town Believes Him",
            "subheadline": "A love story in one purchase, three excuses, and one very direct response",
            "anon_name": "Concerned Biscuit", "category": "Love & Dating",
            "question": "Dear Aunt Lurlene, my husband came home from the pharmacy last week with a box of extra-large condoms. He said he just 'grabbed the wrong box.' He said this with a very specific kind of confidence that made me suspicious. I need to know if I am overthinking this or if something is happening here.",
            "answer": "<p>Sugar, take a deep breath and think about this from a structural perspective. A man who 'grabbed the wrong box' does not walk through the front door with the particular energy you are describing. Men who grab the wrong box say 'oops' and return it. They do not come home proud.</p><p>Now. Does this mean something is happening? Not necessarily. Some men are optimists. Some have been reading things on the internet. Some have simply decided, in the privacy of their own midlife crisis, to re-brand themselves, and this is how that story starts.</p><p>My advice: ask him, directly, with your eyes on his face, and watch what his ears do. Guilty ears do a thing. You'll know.</p>",
        },
        {
            "headline": "Mama Won't Stop Texting My Husband About His Potential — He Has None, She Knows This",
            "subheadline": "A tale of misplaced optimism and aggressive daily motivation",
            "anon_name": "Emotionally Damaged Possum", "category": "Family Drama",
            "question": "My mama has decided my husband has 'potential' and has been texting him motivational quotes every morning for three months. He has no ambitions. He is happy. I am happy. Mama is not happy about how happy we are with how things are.",
            "answer": "<p>What you have here is a mama who loved a project and your husband walked into her sight lines at the wrong moment. She doesn't see a man, she sees a renovation — and renovations are very difficult to stop once a woman like your mama gets her tape measure out.</p><p>The good news is your husband is probably delighted. Men who receive daily encouragement from a woman who believes in them generally do not mind one bit.</p><p>The path forward: let your mama believe she's making progress. Give her one small win — maybe he starts a hobby, maybe an online class he never finishes. That gives her something to do while you and your content husband continue being just fine.</p>",
        },
        {
            "headline": "Man Insists Crocs Are Formal Wear; Wife Has Filed for Emotional Separation",
            "subheadline": "A footwear dispute that has escalated beyond what anyone expected",
            "anon_name": "Troubled in Tampa", "category": "Embarrassing Moments",
            "question": "My husband wore his Crocs to my sister's wedding. He said, and I quote, 'They have a back strap so they're basically dress shoes.' I need Aunt Lurlene to weigh in officially so I have documentation.",
            "answer": "<p>Print this out and laminate it, because I will be saying it once: a back strap does not make a Croc formal wear. A back strap makes a Croc a sandal with ambitions. Those are not the same thing.</p><p>Now, your husband feels comfortable and confident. These are not bad qualities in a man. But there is a time and a place, and that time is not a wedding, and that place is not your sister's reception photos, which will exist forever.</p><p>You have the documentation you requested. Use it in good health.</p>",
        },
    ]
    for p in seed_posts:
        pid = insert_id(
            "INSERT INTO posts (headline, subheadline, anon_name, category, question, answer, published_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (p["headline"], p["subheadline"], p["anon_name"], p["category"], p["question"], p["answer"], now_iso()),
        )
        for r in REACTION_TYPES:
            q("INSERT INTO reactions (post_id, reaction, count) VALUES (%s,%s,%s)",
              (pid, r, random.randint(5, 60)))
    for name, cat, msg in [
        ("Frantic Biscuit", "Work Problems",
         "Dear Aunt Lurlene, my coworker has been microwaving fish in the breakroom every Tuesday and Thursday for six months. HR says it's 'not technically against policy.' I am losing my mind. Please help."),
        ("Nervous Sweet Tea", "Love & Dating",
         "Dear Aunt Lurlene, I accidentally sent my boss a text meant for my husband. My boss responded with 'Noted' and nothing else. It has been four days. I have to go back to work Monday. Please advise."),
    ]:
        q("INSERT INTO submissions (anon_name, category, message, status, created_at) VALUES (%s,%s,%s,'new',%s)",
          (name, cat, msg, now_iso()))
    print("[seed] Done. Aunt Lurlene is open for business.")


# ---- endpoint logic (returns plain data, or (status_code, data) tuples) ----
def api_posts():
    posts = q("SELECT id, headline, subheadline, anon_name, category, question, answer, published_at "
              "FROM posts ORDER BY published_at DESC", (), "all")
    reacts = q("SELECT post_id, reaction, count FROM reactions", (), "all")
    by = {}
    for r in reacts:
        by.setdefault(r["post_id"], {})[r["reaction"]] = r["count"]
    for p in posts:
        p["reactions"] = by.get(p["id"], {})
    return posts


def api_submit(data):
    category = (data.get("category") or "").strip()
    message = (data.get("message") or "").strip()
    photo = (data.get("photo_url") or "").strip() or None
    if not category or not message:
        return (400, {"error": "Pick a category and write something, sugar."})
    anon = random_name()
    q("INSERT INTO submissions (anon_name, category, message, photo_url, status, created_at) "
      "VALUES (%s,%s,%s,%s,'new',%s)", (anon, category, message, photo, now_iso()))
    return (200, {"ok": True, "anon_name": anon})


def api_react(data):
    reaction = data.get("reaction")
    post_id = data.get("post_id")
    if reaction not in REACTION_TYPES:
        return (400, {"error": "Unknown reaction."})
    if USE_PG:
        q("INSERT INTO reactions (post_id, reaction, count) VALUES (%s,%s,1) "
          "ON CONFLICT (post_id, reaction) DO UPDATE SET count = reactions.count + 1", (post_id, reaction))
    else:
        q("INSERT INTO reactions (post_id, reaction, count) VALUES (%s,%s,1) "
          "ON CONFLICT (post_id, reaction) DO UPDATE SET count = count + 1", (post_id, reaction))
    row = q("SELECT count FROM reactions WHERE post_id=%s AND reaction=%s", (post_id, reaction), "one")
    return (200, {"ok": True, "count": row["count"] if row else 1})


def admin_stats():
    def c(sql):
        return q(sql, (), "one")["n"]
    return {
        "new": c("SELECT COUNT(*) AS n FROM submissions WHERE status='new'"),
        "pending": c("SELECT COUNT(*) AS n FROM submissions WHERE status='pending'"),
        "published": c("SELECT COUNT(*) AS n FROM posts"),
        "rejected": c("SELECT COUNT(*) AS n FROM submissions WHERE status IN ('rejected','archived')"),
    }


def admin_submissions(status):
    if status == "rejected":
        return q("SELECT id, anon_name, category, message, photo_url, status, created_at "
                 "FROM submissions WHERE status IN ('rejected','archived') ORDER BY created_at DESC", (), "all")
    return q("SELECT id, anon_name, category, message, photo_url, status, created_at "
             "FROM submissions WHERE status=%s ORDER BY created_at DESC", (status,), "all")


def admin_new_since(since):
    since = since or "1970-01-01T00:00:00+00:00"
    items = q("SELECT id, anon_name, category, created_at FROM submissions "
              "WHERE status='new' AND created_at > %s ORDER BY created_at ASC", (since,), "all")
    return {"now": now_iso(), "items": items}


def admin_status(data):
    status = data.get("status")
    sid = data.get("id")
    if status not in ("pending", "rejected", "archived", "new"):
        return (400, {"error": "Bad status."})
    q("UPDATE submissions SET status=%s WHERE id=%s", (status, sid))
    return (200, {"ok": True})


def admin_answer(data):
    sid = data.get("id")
    answer = (data.get("answer") or "").strip()
    headline = (data.get("headline") or "").strip()
    subheadline = (data.get("subheadline") or "").strip() or None
    if not answer:
        return (400, {"error": "Write an answer first, Lurlene."})
    sub = q("SELECT * FROM submissions WHERE id=%s", (sid,), "one")
    if not sub:
        return (404, {"error": "Submission not found."})
    body = answer.replace("\r\n", "\n")
    html = "<p>" + body.replace("\n\n", "</p><p>").replace("\n", " ") + "</p>"
    final_headline = headline or ("Aunt Lurlene Responds to " + sub["anon_name"])
    pid = insert_id(
        "INSERT INTO posts (submission_id, headline, subheadline, anon_name, category, question, answer, published_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (sub["id"], final_headline, subheadline, sub["anon_name"], sub["category"], sub["message"], html, now_iso()))
    for r in REACTION_TYPES:
        q("INSERT INTO reactions (post_id, reaction, count) VALUES (%s,%s,0)", (pid, r))
    q("UPDATE submissions SET status='published' WHERE id=%s", (sub["id"],))
    return (200, {"ok": True, "post_id": pid})


# ---- the entire web page, embedded (no static HTML file in the repo) ----
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ask Aunt Lurlene</title>
<link rel="icon" href="data:,">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;0,900;1,400;1,700&family=IM+Fell+English:ital@0;1&family=Source+Serif+4:opsz,wght@8..60,300;8..60,400;8..60,600&family=Oswald:wght@400;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --cream:#f5f0e8; --cream-dark:#ede6d5; --ink:#1a1208; --ink-light:#3d2e1a;
  --burgundy:#6b1d2e; --burgundy-light:#8b2d42; --gold:#b8860b; --gold-light:#d4a017;
  --teal:#1a4a4a; --teal-light:#2a6a6a;
  --serif:'Playfair Display',Georgia,serif; --fell:'IM Fell English',Georgia,serif;
  --source:'Source Serif 4',Georgia,serif; --oswald:'Oswald',sans-serif;
}
body{background:var(--cream);color:var(--ink);font-family:var(--source);font-size:16px;line-height:1.6;min-height:100vh}
.paper-texture{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='400' height='400'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E%3Crect width='400' height='400' filter='url(%23n)' opacity='0.035'/%3E%3C/svg%3E")}
.site-nav{background:var(--ink);position:sticky;top:0;z-index:100}
.nav-inner{max-width:1200px;margin:0 auto;display:flex;align-items:center;overflow-x:auto;scrollbar-width:none}
.nav-inner::-webkit-scrollbar{display:none}
.nav-inner a{color:#e8dcc8;font-family:var(--oswald);font-size:13px;letter-spacing:1.5px;text-transform:uppercase;padding:12px 14px;text-decoration:none;white-space:nowrap;border-right:1px solid #333;transition:background .2s;cursor:pointer}
.nav-inner a:hover,.nav-inner a.active{background:var(--burgundy);color:#fff}
.nav-inner a.admin-link{margin-left:auto;background:var(--teal);border-right:none}
.masthead{background:var(--cream);border-bottom:3px double var(--ink);text-align:center;padding:20px 16px 12px}
.masthead-date{font-family:var(--oswald);font-size:12px;letter-spacing:2px;color:var(--ink-light);text-transform:uppercase;margin-bottom:6px}
.masthead-rule{display:flex;align-items:center;gap:12px;justify-content:center;margin:8px 0}
.masthead-rule span{font-size:11px;font-family:var(--oswald);letter-spacing:2px;color:var(--ink-light)}
.masthead-rule::before,.masthead-rule::after{content:'';flex:1;height:1px;background:var(--ink);max-width:200px}
.site-title{font-family:var(--serif);font-size:clamp(42px,8vw,90px);font-weight:900;line-height:1;letter-spacing:-1px;color:var(--ink);margin:4px 0}
.site-tagline{font-family:var(--fell);font-style:italic;font-size:15px;color:var(--ink-light);letter-spacing:1px}
.page{display:none;max-width:1200px;margin:0 auto;padding:24px 16px}
.page.active{display:block}
.lurlene-hero{background:var(--cream-dark);border:3px double var(--ink);margin:24px 0 32px;position:relative;overflow:hidden}
.lurlene-hero::before{content:'';position:absolute;inset:6px;border:1px solid var(--gold);pointer-events:none;z-index:1}
.hero-inner{display:grid;grid-template-columns:1fr 300px;min-height:420px}
.hero-text{padding:40px;position:relative;z-index:2}
.hero-eyebrow{font-family:var(--oswald);font-size:13px;letter-spacing:3px;text-transform:uppercase;color:var(--gold);border-bottom:2px solid var(--gold);display:inline-block;padding-bottom:3px;margin-bottom:16px}
.hero-headline{font-family:var(--serif);font-size:clamp(38px,5vw,62px);font-weight:900;line-height:1.05;margin-bottom:8px}
.hero-subhead{font-family:var(--fell);font-style:italic;font-size:22px;color:var(--burgundy);margin-bottom:20px}
.hero-intro{font-size:16px;line-height:1.7;color:var(--ink-light);margin-bottom:28px;max-width:440px}
.hero-sig{font-family:var(--serif);font-style:italic;font-size:28px;color:var(--burgundy);font-weight:700;margin-bottom:8px}
.hero-sig-line{width:180px;height:1px;background:var(--gold);margin-bottom:4px}
.hero-sig-sub{font-family:var(--oswald);font-size:11px;letter-spacing:2px;color:var(--ink-light);text-transform:uppercase}
.hero-portrait{background:linear-gradient(160deg,#2a1a0a 0%,#4a2a12 40%,#3a1a08 100%);display:flex;flex-direction:column;align-items:center;justify-content:flex-end;padding:32px 24px 24px;position:relative;z-index:2}
.portrait-frame{width:200px;height:220px;border:3px solid var(--gold);background:#2a1a0a;display:flex;align-items:center;justify-content:center;margin-bottom:16px;position:relative;overflow:hidden}
.portrait-frame::after{content:'';position:absolute;inset:8px;border:1px solid rgba(184,134,11,0.4)}
.portrait-caption{font-family:var(--oswald);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--gold);text-align:center}
.btn-ask{display:inline-flex;align-items:center;gap:8px;background:var(--burgundy);color:#fff;font-family:var(--oswald);font-size:15px;letter-spacing:2px;text-transform:uppercase;padding:14px 28px;cursor:pointer;text-decoration:none;transition:background .2s;border:2px solid var(--burgundy-light)}
.btn-ask:hover{background:var(--burgundy-light)}
.btn-secondary{display:inline-flex;align-items:center;gap:8px;background:transparent;color:var(--teal);font-family:var(--oswald);font-size:13px;letter-spacing:2px;text-transform:uppercase;padding:10px 20px;border:2px solid var(--teal);cursor:pointer;text-decoration:none;transition:all .2s}
.btn-secondary:hover{background:var(--teal);color:#fff}
.news-grid{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--ink);margin-bottom:32px}
.news-col{padding:20px;border-right:1px solid var(--ink)}
.news-col:last-child{border-right:none}
.news-col.featured{grid-column:span 2;background:var(--cream-dark)}
.col-label{font-family:var(--oswald);font-size:10px;letter-spacing:3px;text-transform:uppercase;color:var(--burgundy);border-bottom:2px solid var(--burgundy);margin-bottom:12px;padding-bottom:4px}
.post-headline{font-family:var(--serif);font-weight:700;font-size:20px;line-height:1.2;margin-bottom:6px;cursor:pointer;color:var(--ink)}
.post-headline:hover{color:var(--burgundy)}
.post-headline.large{font-size:28px}
.post-byline{font-family:var(--oswald);font-size:11px;letter-spacing:1px;color:var(--ink-light);margin-bottom:8px;text-transform:uppercase}
.post-byline span{color:var(--burgundy)}
.post-body{font-size:14px;line-height:1.65;color:var(--ink-light)}
.post-divider{border:none;border-top:1px solid rgba(26,18,8,0.3);margin:14px 0}
.reactions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.reaction-btn{font-family:var(--oswald);font-size:10px;letter-spacing:1px;padding:4px 10px;border:1px solid var(--ink-light);background:transparent;cursor:pointer;color:var(--ink-light);transition:all .15s;text-transform:uppercase}
.reaction-btn:hover{background:var(--ink);color:var(--cream)}
.reaction-btn.active{background:var(--burgundy);color:#fff;border-color:var(--burgundy)}
.reaction-count{margin-left:4px;opacity:.7}
.section-header{text-align:center;margin:32px 0 24px;position:relative}
.section-header h2{font-family:var(--serif);font-size:32px;font-weight:700;display:inline-block;padding:0 24px;background:var(--cream)}
.section-header::before{content:'';position:absolute;top:50%;left:0;right:0;height:2px;background:var(--ink);z-index:-1}
.section-header.gold::before{background:var(--gold)}
.section-header.gold h2{color:var(--burgundy)}
.ticker-wrap{background:var(--ink);color:#e8dcc8;font-family:var(--oswald);font-size:12px;letter-spacing:1.5px;text-transform:uppercase;padding:6px 0;overflow:hidden;position:relative}
.ticker-label{background:var(--burgundy);padding:6px 16px;position:absolute;left:0;top:0;bottom:0;display:flex;align-items:center;z-index:1;font-weight:600}
.ticker-inner{display:flex;animation:ticker 40s linear infinite;white-space:nowrap;padding-left:160px}
.ticker-inner span{padding:0 40px}
@keyframes ticker{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}
.filler-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1px;background:var(--ink);border:1px solid var(--ink);margin-bottom:32px}
.filler-card{background:var(--cream);padding:16px}
.filler-badge{font-family:var(--oswald);font-size:9px;letter-spacing:2px;text-transform:uppercase;background:var(--gold);color:#fff;padding:2px 8px;margin-bottom:8px;display:inline-block}
.filler-headline{font-family:var(--serif);font-size:17px;font-weight:700;line-height:1.25;margin-bottom:6px}
.filler-body{font-size:13px;line-height:1.55;color:var(--ink-light)}
.classifieds{border:3px double var(--ink);padding:20px;margin-bottom:32px}
.classifieds-title{font-family:var(--serif);font-size:24px;font-weight:900;text-align:center;border-bottom:2px solid var(--ink);margin-bottom:16px;padding-bottom:8px}
.classifieds-grid{columns:3;gap:20px}
@media(max-width:600px){.classifieds-grid{columns:1}}
.classified-item{break-inside:avoid;margin-bottom:14px;padding-bottom:14px;border-bottom:1px dotted var(--ink-light)}
.classified-cat{font-family:var(--oswald);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy)}
.classified-text{font-size:13px;line-height:1.5}
.form-wrap{max-width:700px;margin:0 auto;background:var(--cream-dark);border:3px double var(--ink);padding:40px}
.form-header{text-align:center;margin-bottom:32px}
.form-header h2{font-family:var(--serif);font-size:42px;font-weight:900;line-height:1}
.form-header p{font-family:var(--fell);font-style:italic;font-size:18px;color:var(--burgundy);margin-top:8px}
.form-group{margin-bottom:20px}
.form-group label{display:block;font-family:var(--oswald);font-size:12px;letter-spacing:2px;text-transform:uppercase;margin-bottom:6px;color:var(--ink-light)}
.form-group select,.form-group textarea,.form-group input[type=text]{width:100%;background:var(--cream);border:1px solid var(--ink-light);padding:10px 14px;font-family:var(--source);font-size:15px;color:var(--ink);resize:vertical}
.form-group select:focus,.form-group textarea:focus{outline:2px solid var(--gold);outline-offset:2px}
.anon-name{background:var(--teal);color:#e8dcc8;padding:12px 16px;margin-bottom:20px;font-family:var(--oswald);font-size:14px;letter-spacing:1px}
.anon-name span{font-weight:600;color:var(--gold-light)}
.form-note{font-size:13px;color:var(--ink-light);font-style:italic;margin-top:4px}
.submit-success{background:var(--teal);color:#e8dcc8;padding:24px;text-align:center;margin-top:20px;display:none}
.admin-login{max-width:400px;margin:40px auto;background:var(--cream-dark);border:3px double var(--ink);padding:40px;text-align:center}
.admin-login h2{font-family:var(--serif);font-size:36px;font-weight:900;margin-bottom:8px}
.admin-login p{font-family:var(--fell);font-style:italic;color:var(--burgundy);margin-bottom:24px}
.admin-input{width:100%;background:var(--cream);border:1px solid var(--ink-light);padding:10px 14px;font-family:var(--source);font-size:15px;color:var(--ink);margin-bottom:12px}
.admin-dash{display:none}
.dash-header{display:flex;justify-content:space-between;align-items:center;background:var(--ink);color:#e8dcc8;padding:16px 24px;margin-bottom:24px;flex-wrap:wrap;gap:12px}
.dash-header h2{font-family:var(--serif);font-size:28px;font-weight:700;color:var(--gold)}
.inbox-item{background:var(--cream-dark);border:1px solid var(--ink-light);border-left:4px solid var(--burgundy);padding:20px;margin-bottom:16px}
.inbox-item.answered{border-left-color:var(--teal);opacity:.7}
.inbox-item.rejected{border-left-color:#999;opacity:.6}
.inbox-cat{font-family:var(--oswald);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy);margin-bottom:4px}
.inbox-sender{font-family:var(--serif);font-size:18px;font-weight:700;margin-bottom:6px}
.inbox-preview{font-size:14px;color:var(--ink-light);margin-bottom:12px}
.inbox-actions{display:flex;gap:8px;flex-wrap:wrap}
.inbox-btn{font-family:var(--oswald);font-size:11px;letter-spacing:1.5px;text-transform:uppercase;padding:7px 14px;border:1px solid;cursor:pointer;transition:all .15s}
.inbox-btn.answer{background:var(--burgundy);color:#fff;border-color:var(--burgundy)}
.inbox-btn.answer:hover{background:var(--burgundy-light)}
.inbox-btn.save{background:transparent;color:var(--gold);border-color:var(--gold)}
.inbox-btn.save:hover{background:var(--gold);color:#fff}
.inbox-btn.reject{background:transparent;color:#666;border-color:#999}
.inbox-btn.reject:hover{background:#999;color:#fff}
.inbox-btn.archive{background:transparent;color:var(--teal);border-color:var(--teal)}
.inbox-btn.archive:hover{background:var(--teal);color:#fff}
.answer-panel{display:none;background:var(--cream);border-top:2px solid var(--gold);margin-top:16px;padding:16px}
.answer-panel textarea{width:100%;background:var(--cream-dark);border:1px solid var(--ink-light);padding:10px;font-family:var(--source);font-size:14px;color:var(--ink);resize:vertical;min-height:120px;margin-bottom:10px}
.answer-panel input{width:100%;background:var(--cream-dark);border:1px solid var(--ink-light);padding:8px 10px;font-family:var(--serif);font-size:16px;color:var(--ink);margin-bottom:10px}
.breaking-popup{position:fixed;top:20px;right:20px;background:var(--cream);border:3px solid var(--burgundy);padding:20px 24px;max-width:300px;z-index:9999;display:none;box-shadow:6px 6px 0 var(--ink)}
.breaking-label{font-family:var(--oswald);font-size:11px;letter-spacing:3px;text-transform:uppercase;color:#fff;background:var(--burgundy);padding:3px 10px;margin-bottom:10px;display:inline-block;animation:blink 1s step-end infinite}
@keyframes blink{50%{opacity:.5}}
.breaking-text{font-family:var(--serif);font-size:18px;font-weight:700;line-height:1.3;margin-bottom:14px}
.breaking-close{font-family:var(--oswald);font-size:11px;letter-spacing:1px;text-transform:uppercase;padding:6px 16px;background:var(--ink);color:var(--cream);border:none;cursor:pointer}
.full-post{max-width:720px;margin:0 auto}
.full-post-paper{background:var(--cream-dark);border:2px solid var(--ink);padding:40px;position:relative}
.full-post-paper::before{content:'';position:absolute;inset:6px;border:1px solid rgba(184,134,11,0.3);pointer-events:none}
.full-post-cat{font-family:var(--oswald);font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--burgundy);border-bottom:1px solid var(--burgundy);display:inline-block;padding-bottom:2px;margin-bottom:12px}
.full-post-headline{font-family:var(--serif);font-size:clamp(24px,4vw,38px);font-weight:900;line-height:1.15;margin-bottom:8px}
.full-post-subhead{font-family:var(--fell);font-style:italic;font-size:20px;color:var(--burgundy);margin-bottom:12px}
.full-post-meta{font-family:var(--oswald);font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:var(--ink-light);border-top:2px solid var(--ink);border-bottom:1px solid var(--ink);padding:6px 0;margin-bottom:20px}
.full-post-q{background:var(--cream);border-left:4px solid var(--gold);padding:16px;margin-bottom:20px;font-size:15px;font-style:italic;line-height:1.65}
.full-post-q-label{font-family:var(--oswald);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--gold);margin-bottom:6px}
.full-post-answer{font-size:16px;line-height:1.75;margin-bottom:24px}
.full-post-answer p{margin-bottom:12px}
.full-post-sig{border-top:3px double var(--ink);padding-top:16px;display:flex;justify-content:flex-end}
.sig-block{text-align:right}
.sig-name{font-family:var(--serif);font-style:italic;font-size:26px;font-weight:700;color:var(--burgundy)}
.sig-quote{font-size:13px;font-style:italic;color:var(--ink-light);margin-top:4px}
.pull-quote{border-top:3px solid var(--ink);border-bottom:3px solid var(--ink);padding:16px;margin:20px 0;text-align:center;font-family:var(--serif);font-size:22px;font-style:italic;font-weight:700;color:var(--burgundy);line-height:1.3}
.dots-divider{text-align:center;font-size:20px;letter-spacing:8px;color:var(--gold);margin:16px 0;opacity:.6}
.dash-tabs{display:flex;border-bottom:2px solid var(--ink);margin-bottom:20px;overflow-x:auto}
.dash-tab{font-family:var(--oswald);font-size:12px;letter-spacing:1.5px;text-transform:uppercase;padding:10px 18px;border:none;background:transparent;cursor:pointer;color:var(--ink-light);border-bottom:3px solid transparent;margin-bottom:-2px;white-space:nowrap}
.dash-tab.active{color:var(--burgundy);border-bottom-color:var(--burgundy)}
.dash-content{display:none}
.dash-content.active{display:block}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--ink);border:1px solid var(--ink);margin-bottom:20px}
.stat-box{background:var(--cream);padding:16px;text-align:center}
.stat-num{font-family:var(--serif);font-size:36px;font-weight:900;color:var(--burgundy)}
.stat-label{font-family:var(--oswald);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--ink-light)}
.gossip-item{border-bottom:1px dotted var(--ink-light);padding:12px 0}
.gossip-item:last-child{border-bottom:none}
.gossip-headline{font-family:var(--serif);font-size:17px;font-weight:700;margin-bottom:4px}
.gossip-source{font-family:var(--oswald);font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--gold)}
@media(max-width:768px){
  .hero-inner{grid-template-columns:1fr}
  .hero-portrait{display:none}
  .news-grid{grid-template-columns:1fr}
  .news-col.featured{grid-column:span 1}
  .classifieds-grid{columns:1}
  .hero-text,.form-wrap,.full-post-paper{padding:24px}
  .stats-row{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body class="paper-texture">

<div class="breaking-popup" id="breakingPopup">
  <div class="breaking-label">&#9889; Breaking News</div>
  <div class="breaking-text" id="breakingText">A Worried Biscuit needs advice.</div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="inbox-btn answer" onclick="showPage('admin');closePopup()">Answer Now</button>
    <button class="breaking-close" onclick="closePopup()">Dismiss</button>
  </div>
</div>

<nav class="site-nav"><div class="nav-inner">
  <a onclick="showPage('home')" class="active" id="nav-home">Home</a>
  <a onclick="showPage('ask')" id="nav-ask">Ask Lurlene</a>
  <a onclick="showPage('advice')" id="nav-advice">Latest Advice</a>
  <a onclick="showPage('confessions')" id="nav-confessions">Confessions</a>
  <a onclick="showPage('rants')" id="nav-rants">Lurlene's Rants</a>
  <a onclick="showPage('gossip')" id="nav-gossip">Gossip</a>
  <a onclick="showPage('classifieds')" id="nav-classifieds">Classifieds</a>
  <a onclick="showPage('admin')" id="nav-admin" class="admin-link">Admin &#9656;</a>
</div></nav>

<header class="masthead">
  <div class="masthead-date" id="masthead-date"></div>
  <div class="masthead-rule"><span>&#9733; Est. 2024 &#9733;</span></div>
  <h1 class="site-title">Ask Aunt Lurlene</h1>
  <p class="site-tagline">Southern wisdom, anonymous submissions, and the Lord's honest truth &mdash; whether you want it or not.</p>
  <div class="masthead-rule"><span>Vol. XII &middot; No. 47 &middot; Published Whenever She Feels Like It</span></div>
</header>

<div class="ticker-wrap"><div class="ticker-label">LATEST</div><div class="ticker-inner">
  <span>Local cat elected mayor for third consecutive year &middot;</span>
  <span>Woman discovers gas station sushi was a mistake &middot;</span>
  <span>Man still insisting Crocs are formal wear &middot;</span>
  <span>Aunt Lurlene warns town about microwave fish again &middot;</span>
  <span>Family reunion derailed by controversial potato salad &middot;</span>
  <span>Local man wins argument with stop sign &middot;</span>
  <span>Local cat elected mayor for third consecutive year &middot;</span>
  <span>Woman discovers gas station sushi was a mistake &middot;</span>
  <span>Man still insisting Crocs are formal wear &middot;</span>
  <span>Aunt Lurlene warns town about microwave fish again &middot;</span>
  <span>Family reunion derailed by controversial potato salad &middot;</span>
  <span>Local man wins argument with stop sign &middot;</span>
</div></div>

<!-- HOME -->
<main class="page active" id="page-home">
  <div class="lurlene-hero"><div class="hero-inner">
    <div class="hero-text">
      <div class="hero-eyebrow">Featured Column</div>
      <h2 class="hero-headline">Dear Aunt Lurlene</h2>
      <p class="hero-subhead">"Ask me anything, sugar.<br>I have seen worse."</p>
      <p class="hero-intro">Every town has that one woman who has seen it all, knows everybody's business, and isn't afraid to say so over sweet tea. That's Aunt Lurlene. Send her your questions, confessions, family disasters, and workplace horrors &mdash; all completely anonymous. She'll answer with the honesty your mama was too polite to give you.</p>
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:28px">
        <button class="btn-ask" onclick="showPage('ask')">&#9993; Ask Aunt Lurlene Anonymously</button>
        <button class="btn-secondary" onclick="showPage('advice')">Read Latest Advice &rarr;</button>
      </div>
      <div class="hero-sig-line"></div>
      <div class="hero-sig">Aunt Lurlene</div>
      <div class="hero-sig-sub">Advice Columnist &middot; Truth Teller &middot; Community Icon</div>
    </div>
    <div class="hero-portrait">
      <div class="portrait-frame">
        <svg viewBox="0 0 200 220" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Illustration of Aunt Lurlene">
          <rect width="200" height="220" fill="#2a1a0a"/>
          <ellipse cx="100" cy="200" rx="70" ry="40" fill="#6b1d2e"/>
          <rect x="55" y="145" width="90" height="70" rx="8" fill="#6b1d2e"/>
          <rect x="70" y="140" width="60" height="30" rx="4" fill="#c9b08a"/>
          <rect x="87" y="120" width="26" height="28" rx="6" fill="#c9a070"/>
          <ellipse cx="100" cy="105" rx="42" ry="48" fill="#c9a070"/>
          <ellipse cx="100" cy="72" rx="50" ry="35" fill="#3d2010"/>
          <ellipse cx="68" cy="85" rx="22" ry="30" fill="#3d2010"/>
          <ellipse cx="132" cy="85" rx="22" ry="30" fill="#3d2010"/>
          <ellipse cx="100" cy="60" rx="42" ry="20" fill="#4a2812"/>
          <rect x="75" y="97" width="22" height="14" rx="5" fill="none" stroke="#b8860b" stroke-width="2"/>
          <rect x="103" y="97" width="22" height="14" rx="5" fill="none" stroke="#b8860b" stroke-width="2"/>
          <line x1="97" y1="104" x2="103" y2="104" stroke="#b8860b" stroke-width="2"/>
          <ellipse cx="86" cy="104" rx="6" ry="5" fill="#3d1a0a"/>
          <ellipse cx="114" cy="104" rx="6" ry="5" fill="#3d1a0a"/>
          <circle cx="84" cy="102" r="1.5" fill="#fff" opacity=".8"/>
          <circle cx="112" cy="102" r="1.5" fill="#fff" opacity=".8"/>
          <path d="M78 92 Q86 88 95 91" stroke="#3d1a0a" stroke-width="2" fill="none" stroke-linecap="round"/>
          <path d="M106 91 Q114 88 122 92" stroke="#3d1a0a" stroke-width="2" fill="none" stroke-linecap="round"/>
          <path d="M88 122 Q100 130 112 122" stroke="#8b4a30" stroke-width="2" fill="none" stroke-linecap="round"/>
          <circle cx="58" cy="112" r="5" fill="#b8860b"/>
          <circle cx="142" cy="112" r="5" fill="#b8860b"/>
          <circle cx="82" cy="140" r="3" fill="#e8d8b8"/><circle cx="90" cy="143" r="3" fill="#e8d8b8"/>
          <circle cx="100" cy="145" r="3" fill="#e8d8b8"/><circle cx="110" cy="143" r="3" fill="#e8d8b8"/>
          <circle cx="118" cy="140" r="3" fill="#e8d8b8"/>
          <circle cx="100" cy="155" r="6" fill="#b8860b"/><circle cx="100" cy="155" r="3" fill="#d4a017"/>
        </svg>
      </div>
      <div class="portrait-caption">Aunt Lurlene<br>Advice Columnist</div>
    </div>
  </div></div>

  <div class="section-header gold"><h2>Latest Advice from Aunt Lurlene</h2></div>
  <div id="home-advice"><div style="text-align:center;padding:30px;font-family:var(--fell);font-style:italic;color:var(--ink-light)">Loading the latest from Aunt Lurlene...</div></div>

  <div class="section-header"><h2>Community News &amp; Local Updates</h2></div>
  <div class="filler-grid">
    <div class="filler-card"><div class="filler-badge">&#9888; Humor &middot; Satire</div><div class="filler-headline">Local Cat Elected Mayor for Third Consecutive Year; Council Refuses to Acknowledge Results</div><div class="filler-body">Mr. Biscuits, a gray tabby of no fixed political affiliation, secured a third term 847 to 12, with the 12 opposing votes reportedly coming from dogs.</div></div>
    <div class="filler-card"><div class="filler-badge">&#9888; Humor &middot; Satire</div><div class="filler-headline">Area Woman Discovers Gas Station Sushi Was Not, in Fact, "Fine"</div><div class="filler-body">Brenda Calloway, 44, reports the optimism she felt in aisle three of the QuickFuel did not survive the drive home. She is recovering and "will be more careful."</div></div>
    <div class="filler-card"><div class="filler-badge">&#9888; Humor &middot; Satire</div><div class="filler-headline">Aunt Lurlene Issues Third Annual Warning Regarding Microwave Fish in Shared Workplaces</div><div class="filler-body">The warning will continue annually "until people act right." The perpetrators remain unidentified. They know who they are.</div></div>
    <div class="filler-card"><div class="filler-badge">&#9888; Humor &middot; Satire</div><div class="filler-headline">Family Reunion Called Off After 14-Year Potato Salad Debate Reaches Critical Mass</div><div class="filler-body">The Hendersons have suspended all gatherings pending a third-party ruling on whether sweet pickles belong in potato salad.</div></div>
  </div>

  <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:32px" id="home-bottom">
    <div style="border:1px solid var(--ink);padding:20px">
      <div class="col-label">Local Gossip &amp; Rumor</div>
      <div class="gossip-item"><div class="gossip-headline">Someone at the PTA meeting said what now?</div><div class="gossip-source">Source: A Reliable Biscuit &middot; 2 days ago</div></div>
      <div class="gossip-item"><div class="gossip-headline">The Hendersons' new above-ground pool has become a neighborhood concern</div><div class="gossip-source">Source: Somebody's Cousin &middot; 4 days ago</div></div>
      <div class="gossip-item"><div class="gossip-headline">Pastor Calvin's truck was seen at the casino buffet again</div><div class="gossip-source">Source: Worried Raccoon &middot; 1 week ago</div></div>
      <div style="margin-top:14px"><button class="btn-secondary" onclick="showPage('gossip')" style="font-size:11px">More Gossip &rarr;</button></div>
    </div>
    <div style="border:1px solid var(--ink);padding:20px">
      <div class="col-label">Classified Ads</div>
      <div class="classified-item"><div class="classified-cat">For Sale</div><div class="classified-text"><strong>Wedding dress.</strong> Only worn twice. Third time didn't happen, which turned out to be a blessing. $200, no questions.</div></div>
      <div class="classified-item"><div class="classified-cat">Seeking</div><div class="classified-text"><strong>Looking for my dignity.</strong> Lost at the Olive Garden on Highway 19, Friday ~10pm. Reward offered.</div></div>
      <div style="margin-top:14px"><button class="btn-secondary" onclick="showPage('classifieds')" style="font-size:11px">All Classifieds &rarr;</button></div>
    </div>
  </div>
</main>

<!-- ASK -->
<main class="page" id="page-ask">
  <div class="form-wrap">
    <div class="form-header">
      <div style="font-family:var(--oswald);font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--gold);margin-bottom:8px">Anonymous Submission</div>
      <h2>Write to Aunt Lurlene</h2>
      <p>100% anonymous. No login required. No names asked. A funny anonymous name is assigned for you when you submit.</p>
    </div>
    <div class="form-group">
      <label>Category</label>
      <select id="subCategory">
        <option value="">Select a category...</option>
        <option>Love &amp; Dating</option><option>Family Drama</option><option>Work Problems</option>
        <option>Confessions</option><option>Embarrassing Moments</option><option>Ask Lurlene Anything</option><option>Reader Photos</option>
      </select>
    </div>
    <div class="form-group">
      <label>Your question, confession, or story</label>
      <textarea id="subMessage" rows="8" placeholder="Dear Aunt Lurlene, I need to tell you something..."></textarea>
      <div class="form-note">Names will be changed to protect the guilty. Adult humor is fine. Be kind-ish.</div>
    </div>
    <div class="form-group">
      <label>Optional photo (not required)</label>
      <input type="text" id="subPhoto" placeholder="Paste a direct image URL if you'd like...">
      <div class="form-note">No graphic content. Must be a direct image link ending in .jpg, .png, or .gif</div>
    </div>
    <div style="display:flex;align-items:flex-start;gap:12px;background:var(--cream);border:1px solid var(--gold);padding:14px;margin-bottom:20px">
      <div style="color:var(--gold);font-size:20px;flex-shrink:0">&#9888;</div>
      <div style="font-size:13px;color:var(--ink-light)">No racism, hate speech, threats, harassment of private individuals, doxxing, or graphic sexual/violent content. Aunt Lurlene won't be answering those, and she won't be amused either.</div>
    </div>
    <button class="btn-ask" style="width:100%;justify-content:center" onclick="submitQuestion()">Send It to Aunt Lurlene &#9993;</button>
    <div class="submit-success" id="submitSuccess">
      <div style="font-family:var(--serif);font-size:22px;font-weight:700;color:var(--gold);margin-bottom:8px">Received, sugar.</div>
      <div style="font-size:15px" id="submitSuccessMsg"></div>
    </div>
  </div>
</main>

<!-- ADVICE -->
<main class="page" id="page-advice">
  <div class="section-header gold"><h2>Aunt Lurlene's Advice Column</h2></div>
  <div style="max-width:900px;margin:0 auto" id="advice-list"></div>
</main>

<!-- CONFESSIONS -->
<main class="page" id="page-confessions">
  <div class="section-header"><h2>Reader Confessions</h2></div>
  <div style="max-width:900px;margin:0 auto">
    <div class="news-grid" style="grid-template-columns:1fr 1fr;margin-bottom:24px">
      <div class="news-col"><div class="col-label">Confessions</div><div class="post-headline">I Told My Mother-in-Law Her Casserole Was Delicious for Seven Years</div><div class="post-byline">By <span>Somebody's Cousin</span></div><div class="post-body">It was never delicious. It contained a structural error in the cheese layer. I smiled every single time. She has passed and I need to put this somewhere.</div></div>
      <div class="news-col"><div class="col-label">Confessions</div><div class="post-headline">I Have Been Telling People I Work in "Finance." I Am a Cashier at Cracker Barrel.</div><div class="post-byline">By <span>Lost in Walmart</span></div><div class="post-body">Someone asked what I did and "finance" came out and they seemed impressed and I just... kept going. I have a whole LinkedIn now.</div></div>
    </div>
    <div class="news-grid" style="grid-template-columns:1fr 1fr">
      <div class="news-col"><div class="col-label">Embarrassing Moments</div><div class="post-headline">I Waved Back at Someone for a Full Thirty Seconds Before Realizing They Were Waving at the Person Behind Me</div><div class="post-byline">By <span>Concerned Biscuit</span></div><div class="post-body">This was at a Publix. There were witnesses. I still think about it. That was in 2019.</div></div>
      <div class="news-col"><div class="col-label">Confessions</div><div class="post-headline">My Dog Is Registered as an Emotional Support Animal. He Is Emotionally the Problem.</div><div class="post-byline">By <span>Troubled in Tampa</span></div><div class="post-body">Three mailmen, one couch, and a $68 candle. He is looking at me with contempt right now. I still love him.</div></div>
    </div>
    <div style="text-align:center;margin-top:24px"><button class="btn-ask" onclick="showPage('ask')">Submit Your Own Confession &#9993;</button></div>
  </div>
</main>

<!-- RANTS -->
<main class="page" id="page-rants">
  <div class="section-header gold"><h2>Aunt Lurlene's Rants</h2></div>
  <div style="max-width:720px;margin:0 auto">
    <div class="full-post-paper" style="margin-bottom:24px">
      <div class="full-post-cat">Lurlene's Rant &middot; Vol. III</div>
      <div class="full-post-headline">On the Matter of Grown Adults Who Cannot Merge Onto a Highway</div>
      <div class="full-post-meta">Published &middot; May 2025 &middot; By Aunt Lurlene herself</div>
      <div class="full-post-answer"><p>I have been driving on this earth for forty-three years and I need somebody to explain why some people pull onto an on-ramp like they are joining a funeral procession. It is a <em>merge lane</em>, not a suggestion, not a little side road where you go park your anxieties while the rest of us blow past at seventy.</p><p>You have one job: match speed. The road gave you a whole runway. And still &mdash; STILL &mdash; here you come doing forty-two with your hazards on for no earthly reason.</p><p>Bless your heart. But also accelerate.</p></div>
      <div class="full-post-sig"><div class="sig-block"><div class="sig-name">Aunt Lurlene</div><div class="sig-quote">"I say this with love. Mostly."</div></div></div>
    </div>
    <div class="full-post-paper">
      <div class="full-post-cat">Lurlene's Rant &middot; Vol. II</div>
      <div class="full-post-headline">A Word on People Who Don't Push Their Cart All the Way Into the Cart Return</div>
      <div class="full-post-meta">Published &middot; April 2025 &middot; By Aunt Lurlene herself</div>
      <div class="full-post-answer"><p>There is a special place in the afterlife, and I will not say where, for people who push their cart to within six feet of the cart return and then just let it go. Just <em>release</em> it. Like it's a little boat you're setting free on a river.</p><p>You have done approximately seventy percent of a part and then abandoned the project. The cart return is RIGHT THERE. I have seen people walk further for a cracker sample they didn't want.</p><p>I will not be taking questions at this time.</p></div>
      <div class="full-post-sig"><div class="sig-block"><div class="sig-name">Aunt Lurlene</div><div class="sig-quote">"The truth is free. The cart corral is right there."</div></div></div>
    </div>
  </div>
</main>

<!-- GOSSIP -->
<main class="page" id="page-gossip">
  <div class="section-header"><h2>Local Gossip &amp; Community Rumors</h2></div>
  <div style="max-width:900px;margin:0 auto">
    <div style="background:var(--cream-dark);border:1px solid var(--gold);padding:12px 16px;margin-bottom:20px;font-family:var(--fell);font-style:italic;font-size:14px;color:var(--ink-light)">All gossip published anonymously and for entertainment. Aunt Lurlene does not confirm or deny. But she does have opinions.</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;border:1px solid var(--ink)">
      <div style="border-right:1px solid var(--ink);padding:20px">
        <div class="col-label">Fresh Gossip</div>
        <div class="gossip-item"><div class="gossip-headline">Someone at the PTA meeting told Karen Fielding the truth about herself and the room has not recovered</div><div class="gossip-source">Source: Reliable Biscuit &middot; 2 days ago</div></div>
        <div class="gossip-item"><div class="gossip-headline">The new couple on Oak Street has been running their leaf blower at 7am on Saturdays. The neighborhood has gone quiet in the way that means plans are being made.</div><div class="gossip-source">Source: Worried Raccoon &middot; 5 days ago</div></div>
        <div class="gossip-item"><div class="gossip-headline">Pastor Calvin's truck was at the casino buffet for four hours and he told everybody he was "doing outreach"</div><div class="gossip-source">Source: Somebody's Cousin &middot; 1 week ago</div></div>
      </div>
      <div style="padding:20px">
        <div class="col-label">Developing Situations</div>
        <div class="gossip-item"><div class="gossip-headline">The Henderson above-ground pool has now been cited by three neighbors. The Hendersons maintain that a flamingo float is not "a structure."</div><div class="gossip-source">Source: Lost in Walmart &middot; 3 days ago</div></div>
        <div class="gossip-item"><div class="gossip-headline">An unnamed local man called himself a "grillmaster" and was then unable to light the grill for twenty-five minutes. He blames the propane. The propane was fine.</div><div class="gossip-source">Source: Concerned Biscuit &middot; 2 weeks ago</div></div>
        <div class="gossip-item"><div class="gossip-headline">Somebody's mama has been "liking" her ex-husband's Facebook posts from 2016 and yes, everybody has noticed</div><div class="gossip-source">Source: Emotionally Damaged Possum &middot; 2 weeks ago</div></div>
      </div>
    </div>
    <div style="text-align:center;margin-top:24px"><button class="btn-ask" onclick="showPage('ask')">Submit Your Gossip Anonymously &#9993;</button></div>
  </div>
</main>

<!-- CLASSIFIEDS -->
<main class="page" id="page-classifieds">
  <div class="section-header"><h2>Classified Advertisements</h2></div>
  <div class="classifieds" style="max-width:900px;margin:0 auto">
    <div class="classifieds-title">COMMUNITY CLASSIFIED ADS</div>
    <div style="font-family:var(--oswald);font-size:10px;letter-spacing:2px;text-align:center;color:var(--ink-light);margin-bottom:16px;text-transform:uppercase">These are real fake advertisements. Some are funnier than others. That is how it goes.</div>
    <div class="classifieds-grid">
      <div class="classified-item"><div class="classified-cat">For Sale</div><div class="classified-text"><strong>Wedding dress.</strong> Size 8. Worn twice &mdash; once for the wedding, once for a formal apology. $200. No questions.</div></div>
      <div class="classified-item"><div class="classified-cat">Lost</div><div class="classified-text"><strong>My dignity.</strong> Last seen at the Olive Garden on Highway 19, ~10:15pm Friday. Modest reward offered.</div></div>
      <div class="classified-item"><div class="classified-cat">Announcement</div><div class="classified-text"><strong>Brent is no longer welcome</strong> at Thursday poker night. He knows. We all know. We are moving forward without him.</div></div>
      <div class="classified-item"><div class="classified-cat">Services</div><div class="classified-text"><strong>Will listen to your problems</strong> for $15/hour. Not a therapist. But I'll make you good coffee and won't tell anybody.</div></div>
      <div class="classified-item"><div class="classified-cat">For Sale</div><div class="classified-text"><strong>Exercise bike.</strong> Lightly used. Currently an expensive clothes hanger. One owner who had big plans. $75 obo.</div></div>
      <div class="classified-item"><div class="classified-cat">Seeking</div><div class="classified-text"><strong>A man who loads the dishwasher correctly</strong> the first time without being shown. Serious inquiries only.</div></div>
      <div class="classified-item"><div class="classified-cat">Warning</div><div class="classified-text"><strong>Do not buy the gas station sushi.</strong> Public service announcement. It is what it appears to be. You've been warned.</div></div>
      <div class="classified-item"><div class="classified-cat">Free</div><div class="classified-text"><strong>Emotional baggage.</strong> Well-organized, labeled. Better shape than most. Must take all of it. No returns.</div></div>
      <div class="classified-item"><div class="classified-cat">Situation</div><div class="classified-text"><strong>I have seventeen jars of pasta sauce</strong> and no memory of buying them. Something happened in the Publix. Please call.</div></div>
    </div>
  </div>
</main>

<!-- FULL POST -->
<main class="page" id="page-post"><div class="full-post" id="post-content"></div></main>

<!-- ADMIN -->
<main class="page" id="page-admin">
  <div class="admin-login" id="adminLogin">
    <div style="font-family:var(--oswald);font-size:12px;letter-spacing:3px;text-transform:uppercase;color:var(--gold);margin-bottom:8px">Staff Only</div>
    <h2>Aunt Lurlene's Office</h2>
    <p>Log in to manage submissions and write responses.</p>
    <input type="text" class="admin-input" id="adminUser" placeholder="Username" autocomplete="username">
    <input type="password" class="admin-input" id="adminPass" placeholder="Password" autocomplete="current-password" onkeydown="if(event.key==='Enter')adminLogin()">
    <button class="btn-ask" style="width:100%;justify-content:center;margin-top:4px" onclick="adminLogin()">Enter the Office</button>
    <div id="loginError" style="color:var(--burgundy);font-family:var(--oswald);font-size:12px;letter-spacing:1px;margin-top:12px;min-height:16px"></div>
  </div>
  <div class="admin-dash" id="adminDash">
    <div class="dash-header">
      <h2>Aunt Lurlene's Dashboard</h2>
      <div style="display:flex;gap:12px;align-items:center">
        <span style="font-family:var(--oswald);font-size:12px;letter-spacing:1px;color:#c9a070">Logged in as Angie</span>
        <button onclick="adminLogout()" style="background:var(--burgundy);color:#fff;border:none;padding:6px 14px;font-family:var(--oswald);font-size:11px;letter-spacing:1px;text-transform:uppercase;cursor:pointer">Log Out</button>
      </div>
    </div>
    <div class="stats-row">
      <div class="stat-box"><div class="stat-num" id="stat-new">0</div><div class="stat-label">New</div></div>
      <div class="stat-box"><div class="stat-num" id="stat-pending">0</div><div class="stat-label">Pending</div></div>
      <div class="stat-box"><div class="stat-num" id="stat-published">0</div><div class="stat-label">Published</div></div>
      <div class="stat-box"><div class="stat-num" id="stat-rejected">0</div><div class="stat-label">Rejected</div></div>
    </div>
    <div class="dash-tabs">
      <button class="dash-tab active" data-tab="new" onclick="switchTab('new')">New Submissions</button>
      <button class="dash-tab" data-tab="pending" onclick="switchTab('pending')">Pending</button>
      <button class="dash-tab" data-tab="published" onclick="switchTab('published')">Published</button>
      <button class="dash-tab" data-tab="rejected" onclick="switchTab('rejected')">Rejected</button>
    </div>
    <div class="dash-content active" id="tab-new"></div>
    <div class="dash-content" id="tab-pending"></div>
    <div class="dash-content" id="tab-published"></div>
    <div class="dash-content" id="tab-rejected"></div>
  </div>
</main>

<script>
const REACTIONS=["Bless Your Heart","Lord Have Mercy","That's Wild","I Need More Tea","Pray For Them","Auntie Was Right"];
let POSTS=[];
let isAdmin=false;
let lastAlertCheck=new Date().toISOString();
let alertTimer=null;

function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function setDate(){
  const d=new Date();
  const days=['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
  const months=['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('masthead-date').textContent=days[d.getDay()]+', '+months[d.getMonth()]+' '+d.getDate()+', '+d.getFullYear();
}
setDate();

function showPage(p){
  document.querySelectorAll('.page').forEach(el=>el.classList.remove('active'));
  document.querySelectorAll('.nav-inner a').forEach(el=>el.classList.remove('active'));
  document.getElementById('page-'+p).classList.add('active');
  const navEl=document.getElementById('nav-'+p);
  if(navEl)navEl.classList.add('active');
  window.scrollTo(0,0);
  if(p==='advice')renderAdviceList();
  if(p==='admin'&&isAdmin)refreshDashboard();
}

// ---------- Public posts ----------
async function loadPosts(){
  try{
    const r=await fetch('/api/posts');
    POSTS=await r.json();
    renderHomeAdvice();
    if(document.getElementById('page-advice').classList.contains('active'))renderAdviceList();
  }catch(e){console.error(e);}
}

function reactionsBar(post,prefix){
  return REACTIONS.map(r=>{
    const c=(post.reactions&&post.reactions[r])||0;
    return `<button class="reaction-btn" onclick="react(${post.id},'${r.replace(/'/g,"\\'")}',this)">${r}<span class="reaction-count">${c}</span></button>`;
  }).join('');
}

async function react(postId,reaction,btn){
  if(btn.classList.contains('active'))return;
  btn.classList.add('active');
  const span=btn.querySelector('.reaction-count');
  span.textContent=parseInt(span.textContent||'0')+1;
  try{await fetch('/api/react',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({post_id:postId,reaction})});}
  catch(e){console.error(e);}
}

function renderHomeAdvice(){
  const el=document.getElementById('home-advice');
  if(!POSTS.length){el.innerHTML='<div style="text-align:center;padding:30px;font-family:var(--fell);font-style:italic;color:var(--ink-light)">No columns published yet. Be the first to write in!</div>';return;}
  const feat=POSTS[0];
  const rest=POSTS.slice(1,4);
  let html='<div class="news-grid"><div class="news-col featured">';
  html+=`<div class="col-label">Featured &middot; ${esc(feat.category)}</div>`;
  html+=`<div class="post-headline large" onclick="showPost(${feat.id})">${esc(feat.headline)}</div>`;
  html+=`<div class="post-byline">By <span>${esc(feat.anon_name)}</span> &middot; ${esc(feat.category)}</div>`;
  html+=`<div class="post-body">${esc(feat.question).slice(0,220)}...</div>`;
  html+=`<div class="reactions">${reactionsBar(feat)}</div></div>`;
  html+='<div class="news-col"><div class="col-label">More Advice</div>';
  if(rest.length){
    rest.forEach((p,i)=>{
      html+=`<div class="post-headline" onclick="showPost(${p.id})">${esc(p.headline)}</div>`;
      html+=`<div class="post-byline">By <span>${esc(p.anon_name)}</span></div>`;
      html+=`<div class="post-body">${esc(p.question).slice(0,90)}...</div>`;
      if(i<rest.length-1)html+='<hr class="post-divider">';
    });
  }else{html+='<div class="post-body" style="font-style:italic">More columns coming soon, sugar.</div>';}
  html+='</div></div>';
  el.innerHTML=html;
}

function renderAdviceList(){
  const el=document.getElementById('advice-list');
  if(!POSTS.length){el.innerHTML='<div style="text-align:center;padding:40px;font-family:var(--fell);font-style:italic;color:var(--ink-light)">No advice columns yet. Submit a question and Aunt Lurlene will get to it.</div>';return;}
  el.innerHTML=POSTS.map(p=>`
    <div style="background:var(--cream-dark);border:1px solid var(--ink);border-left:5px solid var(--burgundy);padding:24px;margin-bottom:20px">
      <div style="font-family:var(--oswald);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy);margin-bottom:6px">${esc(p.category)} &middot; ${new Date(p.published_at).toLocaleDateString('en-US',{year:'numeric',month:'long',day:'numeric'})}</div>
      <div class="post-headline large" style="margin-bottom:6px" onclick="showPost(${p.id})">${esc(p.headline)}</div>
      <div class="post-byline">By <span style="color:var(--burgundy)">${esc(p.anon_name)}</span></div>
      <div style="font-size:14px;color:var(--ink-light);margin:10px 0 14px">${esc(p.question).slice(0,150)}...</div>
      <button class="btn-secondary" onclick="showPost(${p.id})" style="font-size:11px">Read Aunt Lurlene's Answer &rarr;</button>
    </div>`).join('');
}

function showPost(id){
  const p=POSTS.find(x=>x.id===id);
  if(!p)return;
  document.getElementById('post-content').innerHTML=`
    <div style="margin-bottom:16px"><button onclick="showPage('advice')" class="btn-secondary" style="font-size:11px">&larr; Back to Advice</button></div>
    <div class="full-post-paper">
      <div class="full-post-cat">${esc(p.category)}</div>
      <div class="full-post-headline">${esc(p.headline)}</div>
      ${p.subheadline?`<div class="full-post-subhead">${esc(p.subheadline)}</div>`:''}
      <div class="full-post-meta">By ${esc(p.anon_name)} &nbsp;&middot;&nbsp; ${new Date(p.published_at).toLocaleDateString('en-US',{year:'numeric',month:'long',day:'numeric'})} &nbsp;&middot;&nbsp; Ask Aunt Lurlene</div>
      <div class="full-post-q"><div class="full-post-q-label">The Question</div>${esc(p.question)}</div>
      <div style="font-family:var(--oswald);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy);margin-bottom:12px">Aunt Lurlene Says:</div>
      <div class="full-post-answer">${p.answer}</div>
      <div class="dots-divider">&middot; &middot; &middot;</div>
      <div class="full-post-sig"><div class="sig-block"><div class="sig-name">Aunt Lurlene</div><div class="sig-quote">"Bless your heart, but I'm still gonna tell the truth."</div></div></div>
    </div>
    <div class="reactions" style="margin-top:20px">${reactionsBar(p)}</div>`;
  showPage('post');
}

// ---------- Submission ----------
async function submitQuestion(){
  const message=document.getElementById('subMessage').value.trim();
  const category=document.getElementById('subCategory').value;
  const photo_url=document.getElementById('subPhoto').value.trim();
  if(!category||!message){alert('Please pick a category and write your question, sugar.');return;}
  try{
    const r=await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({category,message,photo_url})});
    const data=await r.json();
    if(!r.ok){alert(data.error||'Something went wrong.');return;}
    document.getElementById('submitSuccessMsg').innerHTML=`You've been published anonymously as <strong style="color:var(--gold)">${esc(data.anon_name)}</strong>. Aunt Lurlene will get to it when she gets to it &mdash; keep an eye on the front page for your answer.`;
    document.getElementById('submitSuccess').style.display='block';
    document.getElementById('subMessage').value='';
    document.getElementById('subCategory').value='';
    document.getElementById('subPhoto').value='';
    setTimeout(()=>{document.getElementById('submitSuccess').style.display='none';},9000);
  }catch(e){alert('Could not reach Aunt Lurlene. Try again.');}
}

// ---------- Admin ----------
async function checkSession(){
  try{const r=await fetch('/api/me');const d=await r.json();isAdmin=d.loggedIn;if(isAdmin)enterDashboard();}catch(e){}
}

async function adminLogin(){
  const username=document.getElementById('adminUser').value.trim();
  const password=document.getElementById('adminPass').value;
  const errEl=document.getElementById('loginError');
  errEl.textContent='';
  try{
    const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});
    const d=await r.json();
    if(!r.ok){errEl.textContent=d.error||'Login failed.';return;}
    isAdmin=true;
    document.getElementById('adminPass').value='';
    enterDashboard();
  }catch(e){errEl.textContent='Could not reach the server.';}
}

function enterDashboard(){
  document.getElementById('adminLogin').style.display='none';
  document.getElementById('adminDash').style.display='block';
  refreshDashboard();
  startAlertPolling();
}

async function adminLogout(){
  await fetch('/api/logout',{method:'POST'});
  isAdmin=false;
  stopAlertPolling();
  document.getElementById('adminLogin').style.display='block';
  document.getElementById('adminDash').style.display='none';
}

function switchTab(t){
  document.querySelectorAll('.dash-tab').forEach(el=>el.classList.toggle('active',el.dataset.tab===t));
  document.querySelectorAll('.dash-content').forEach(el=>el.classList.remove('active'));
  document.getElementById('tab-'+t).classList.add('active');
  loadTab(t);
}

async function refreshDashboard(){
  try{
    const s=await (await fetch('/api/admin/stats')).json();
    document.getElementById('stat-new').textContent=s.new;
    document.getElementById('stat-pending').textContent=s.pending;
    document.getElementById('stat-published').textContent=s.published;
    document.getElementById('stat-rejected').textContent=s.rejected;
  }catch(e){}
  const active=document.querySelector('.dash-tab.active');
  loadTab(active?active.dataset.tab:'new');
}

async function loadTab(status){
  const el=document.getElementById('tab-'+status);
  if(status==='published'){
    const posts=POSTS;
    el.innerHTML=posts.length?posts.map(p=>`
      <div class="inbox-item answered">
        <div class="inbox-cat">${esc(p.category)} &middot; ${new Date(p.published_at).toLocaleDateString()}</div>
        <div class="inbox-sender">${esc(p.anon_name)}</div>
        <div class="inbox-preview" style="font-weight:600">${esc(p.headline)}</div>
      </div>`).join(''):'<div style="text-align:center;padding:30px;font-family:var(--fell);font-style:italic;color:var(--ink-light)">Nothing published yet.</div>';
    return;
  }
  try{
    const subs=await (await fetch('/api/admin/submissions?status='+status)).json();
    if(!subs.length){el.innerHTML='<div style="text-align:center;padding:40px;font-family:var(--fell);font-style:italic;color:var(--ink-light)">Nothing here right now. Go outside, Angie.</div>';return;}
    el.innerHTML=subs.map(s=>renderInboxItem(s,status)).join('');
  }catch(e){el.innerHTML='<div style="padding:20px;color:var(--burgundy)">Could not load submissions.</div>';}
}

function renderInboxItem(s,status){
  const cls=status==='rejected'?'inbox-item rejected':'inbox-item';
  let actions='';
  if(status==='new'||status==='pending'){
    actions=`
      <button class="inbox-btn answer" onclick="toggleAnswer(${s.id})">Answer Now</button>
      <button class="inbox-btn save" onclick="setStatus(${s.id},'pending')">Save For Later</button>
      <button class="inbox-btn reject" onclick="setStatus(${s.id},'rejected')">Reject</button>
      <button class="inbox-btn archive" onclick="setStatus(${s.id},'archived')">Archive</button>`;
  }else{
    actions=`<button class="inbox-btn save" onclick="setStatus(${s.id},'new')">Restore to Inbox</button>`;
  }
  return `
    <div class="${cls}" id="inbox-${s.id}">
      <div class="inbox-cat">${esc(s.category)} &middot; ${new Date(s.created_at).toLocaleString()}</div>
      <div class="inbox-sender">${esc(s.anon_name)}</div>
      <div class="inbox-preview">${esc(s.message)}</div>
      ${s.photo_url?`<div style="margin-bottom:10px"><img src="${esc(s.photo_url)}" alt="reader photo" style="max-width:200px;max-height:160px;border:1px solid var(--ink-light)"></div>`:''}
      <div class="inbox-actions">${actions}</div>
      <div class="answer-panel" id="answer-panel-${s.id}">
        <div style="font-family:var(--oswald);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy);margin-bottom:8px">Headline</div>
        <input id="headline-${s.id}" placeholder="Write a newspaper-style headline...">
        <div style="font-family:var(--oswald);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy);margin-bottom:8px">Subheadline (optional)</div>
        <input id="sub-${s.id}" placeholder="A witty subheadline...">
        <div style="font-family:var(--oswald);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--burgundy);margin-bottom:8px">Aunt Lurlene's Answer</div>
        <textarea id="ans-${s.id}" rows="6" placeholder="Write your response, sugar. Press Enter twice for a new paragraph."></textarea>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="inbox-btn answer" onclick="publishAnswer(${s.id})">Publish to Everyone</button>
          <button class="inbox-btn reject" onclick="toggleAnswer(${s.id})">Cancel</button>
        </div>
      </div>
    </div>`;
}

function toggleAnswer(id){
  const p=document.getElementById('answer-panel-'+id);
  p.style.display=p.style.display==='block'?'none':'block';
}

async function setStatus(id,status){
  try{
    await fetch('/api/admin/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,status})});
    refreshDashboard();
  }catch(e){alert('Update failed.');}
}

async function publishAnswer(id){
  const headline=document.getElementById('headline-'+id).value;
  const subheadline=document.getElementById('sub-'+id).value;
  const answer=document.getElementById('ans-'+id).value;
  if(!answer.trim()){alert('Write an answer first, Angie.');return;}
  try{
    const r=await fetch('/api/admin/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,headline,subheadline,answer})});
    const d=await r.json();
    if(!r.ok){alert(d.error||'Publish failed.');return;}
    await loadPosts();          // refresh public column for everyone
    refreshDashboard();         // refresh admin stats/inbox
    alert('Published! Every visitor will now see this column, sugar.');
  }catch(e){alert('Publish failed.');}
}

// ---------- Breaking-news alerts (ADMIN ONLY) ----------
function startAlertPolling(){
  stopAlertPolling();
  lastAlertCheck=new Date().toISOString();
  alertTimer=setInterval(checkForNew,6000);
}
function stopAlertPolling(){if(alertTimer){clearInterval(alertTimer);alertTimer=null;}}

async function checkForNew(){
  if(!isAdmin)return;
  try{
    const r=await fetch('/api/admin/new-since?since='+encodeURIComponent(lastAlertCheck));
    if(!r.ok)return;
    const d=await r.json();
    lastAlertCheck=d.now;
    if(d.items&&d.items.length){
      const newest=d.items[d.items.length-1];
      showBreakingNews(newest.anon_name);
      refreshDashboard();
    }
  }catch(e){}
}

function showBreakingNews(name){
  const msgs=["A "+name+" needs advice.","Breaking: "+name+" has written in.",name+" has a confession. Details developing.","Urgent dispatch from "+name+".",name+" says it's complicated. It is."];
  document.getElementById('breakingText').textContent=msgs[Math.floor(Math.random()*msgs.length)];
  document.getElementById('breakingPopup').style.display='block';
  setTimeout(closePopup,12000);
}
function closePopup(){document.getElementById('breakingPopup').style.display='none';}

// ---------- Boot ----------
loadPosts();
checkSession();
setInterval(loadPosts,30000); // public column auto-refreshes so everyone stays in sync
</script>
</body>
</html>
"""


# =============================== HTTP SERVER ================================

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # keep the deploy logs quiet

    def _send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra:
                self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj), "application/json", extra)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    def _token(self):
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "lurlene_session":
                    return v
        return None

    def _is_admin(self):
        return self._token() in SESSIONS

    def _deny(self):
        self._json(401, {"error": "Not logged in, sugar."})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        params = urllib.parse.parse_qs(u.query)
        if path == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self._send(204)
        if path == "/api/posts":
            return self._json(200, api_posts())
        if path == "/api/me":
            return self._json(200, {"loggedIn": self._is_admin()})
        if path == "/api/admin/stats":
            return self._json(200, admin_stats()) if self._is_admin() else self._deny()
        if path == "/api/admin/submissions":
            if not self._is_admin():
                return self._deny()
            return self._json(200, admin_submissions(params.get("status", ["new"])[0]))
        if path == "/api/admin/new-since":
            if not self._is_admin():
                return self._deny()
            return self._json(200, admin_new_since(params.get("since", [""])[0]))
        # anything else -> serve the page (single-page app)
        return self._send(200, HTML, "text/html; charset=utf-8")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        data = self._read_json()
        if path == "/api/submit":
            return self._json(*api_submit(data))
        if path == "/api/react":
            return self._json(*api_react(data))
        if path == "/api/login":
            if data.get("username") == ADMIN_USER and data.get("password") == ADMIN_PASS:
                token = secrets.token_hex(24)
                SESSIONS.add(token)
                cookie = f"lurlene_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=28800"
                return self._json(200, {"ok": True}, [("Set-Cookie", cookie)])
            return self._json(401, {"error": "Wrong username or password, hon."})
        if path == "/api/logout":
            SESSIONS.discard(self._token())
            return self._json(200, {"ok": True},
                              [("Set-Cookie", "lurlene_session=; Path=/; HttpOnly; Max-Age=0")])
        if path == "/api/admin/status":
            return self._json(*admin_status(data)) if self._is_admin() else self._deny()
        if path == "/api/admin/answer":
            return self._json(*admin_answer(data)) if self._is_admin() else self._deny()
        return self._json(404, {"error": "Not found."})


def main():
    init_db()
    port = int(os.environ.get("PORT", 3000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Aunt Lurlene is live on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
