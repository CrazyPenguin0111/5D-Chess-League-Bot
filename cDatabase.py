import asyncio, csv, os, sqlite3
from datetime import datetime, timedelta

ROLES_CONFIG_FILE = "elo_roles.csv"
sqliteFile = "elo_bot.db"


def init_db():
    conn = sqlite3.connect(sqliteFile)
    c = conn.cursor()

    c.execute(
        """CREATE TABLE IF NOT EXISTS players
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY,
                     elo
                     INTEGER
                     DEFAULT
                     1380,
                     wins
                     INTEGER
                     DEFAULT
                     0,
                     losses
                     INTEGER
                     DEFAULT
                     0,
                     draws
                     INTEGER
                     DEFAULT
                     0,
                     signed_up
                     INTEGER
                     DEFAULT
                     0
                 )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS pending_reps
                 (
                     id
                     INTEGER
                     PRIMARY
                     KEY
                     AUTOINCREMENT,
                     pairing_id
                     INTEGER,
                     reporter_id
                     INTEGER,
                     result
                     TEXT,
                     game_number
                     INTEGER,
                     timestamp
                     DATETIME
                     DEFAULT
                     CURRENT_TIMESTAMP
                 )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS seasons
                 (
                     season_number
                     INTEGER
                     PRIMARY
                     KEY,
                     active
                     INTEGER
                     DEFAULT
                     0
                 )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS pairings
    (
        id
        INTEGER
        PRIMARY
        KEY
        AUTOINCREMENT,
        player1_id
        INTEGER,
        player2_id
        INTEGER,
        result1
        REAL
        DEFAULT
        NULL,
        result2
        REAL
        DEFAULT
        NULL,
        season_number
        INTEGER,
        group_name
        TEXT,
        FOREIGN
        KEY
                 (
        player1_id
                 ) REFERENCES players
                 (
                     id
                 ),
        FOREIGN KEY
                 (
                     player2_id
                 ) REFERENCES players
                 (
                     id
                 )
        )"""
    )

    c.execute(
        """INSERT
    OR IGNORE INTO seasons (season_number, active) VALUES (1, 0)"""
    )

    c.execute("PRAGMA table_info(pairings)")
    columns = [col[1] for col in c.fetchall()]

    if "result1" not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN result1 REAL DEFAULT NULL")
    if "result2" not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN result2 REAL DEFAULT NULL")
    if "season_number" not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN season_number INTEGER")
    if "group_name" not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN group_name TEXT")

    if "signed_up" not in [
        col[1] for col in c.execute("PRAGMA table_info(players)").fetchall()
    ]:
        c.execute("ALTER TABLE players ADD COLUMN signed_up INTEGER DEFAULT 0")

    conn.commit()
    conn.close()


def delete_pending_rep(rep_id):

    conn = sqlite3.connect(sqliteFile)
    c = conn.cursor()
    c.execute("DELETE FROM pending_reps WHERE id=?", (rep_id,))
    conn.commit()
    conn.close()


def update_player_stats(player_id, elo, wins=0, losses=0, draws=0):
    conn = sqlite3.connect(sqliteFile)
    c = conn.cursor()
    c.execute(
        """UPDATE players
                 SET elo=?,
                     wins=wins + ?,
                     losses=losses + ?,
                     draws=draws + ?
                 WHERE id = ?""",
        (elo, wins, losses, draws, player_id),
    )
    conn.commit()
    conn.close()


def get_player_data(player_id):
    conn = sqlite3.connect(sqliteFile)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE id=?", (player_id,))
    player = c.fetchone()
    conn.close()
    return player


async def clean_old_pending_matches():
    while True:
        try:
            conn = sqlite3.connect(sqliteFile)
            c = conn.cursor()
            cutoff_time = datetime.now() - timedelta(minutes=30)
            cutoff_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")
            c.execute("DELETE FROM pending_reps WHERE timestamp < ?", (cutoff_str,))
            deleted_count = c.rowcount
            conn.commit()
            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} old pending matches")
            conn.close()
        except Exception as e:
            print(f"Error cleaning pending matches: {e}")
        await asyncio.sleep(1800)


async def generate_pairings(ctx, season_number):
    try:
        conn = sqlite3.connect(sqliteFile)
        c = conn.cursor()

        c.execute(
            """SELECT id, elo
                     FROM players
                     WHERE signed_up = 1"""
        )
        players = c.fetchall()

        if not players:
            await ctx.send("❌ No players have signed up for the season!")
            return False

        role_ranges = []
        if os.path.exists(ROLES_CONFIG_FILE):
            with open(ROLES_CONFIG_FILE, mode="r") as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    if (
                        not row.get("role")
                        or not row.get("min elo")
                        or not row.get("max elo")
                    ):
                        continue
                    try:
                        role_ranges.append(
                            {
                                "name": row["role"].strip(),
                                "min": int(row["min elo"]),
                                "max": int(row["max elo"]),
                            }
                        )
                    except ValueError:
                        continue

        role_ranges.sort(key=lambda x: x["min"], reverse=True)

        groups = {}
        for player_id, elo in players:

            for role_range in role_ranges:
                if role_range["min"] <= elo <= role_range["max"]:
                    if role_range["name"] not in groups:
                        groups[role_range["name"]] = []
                    groups[role_range["name"]].append(player_id)
                    break

        if not groups:
            await ctx.send("❌ Couldn't group players by league roles!")
            return False

        total_pairings = 0
        for group_name, player_ids in groups.items():

            subgroups = []
            if len(player_ids) > 7:

                import random

                random.shuffle(player_ids)
                subgroup_size = max(4, len(player_ids) // ((len(player_ids) // 7) + 1))
                subgroups = [
                    player_ids[i : i + subgroup_size]
                    for i in range(0, len(player_ids), subgroup_size)
                ]
            else:
                subgroups = [player_ids]

            for i, subgroup in enumerate(subgroups):
                subgroup_name = (
                    group_name if len(subgroups) == 1 else f"{group_name}-{i + 1}"
                )

                from itertools import combinations

                pairings = list(combinations(subgroup, 2))

                for p1, p2 in pairings:

                    c.execute(
                        """INSERT INTO pairings
                                     (player1_id, player2_id, season_number, group_name)
                                 VALUES (?, ?, ?, ?)""",
                        (p1, p2, season_number, subgroup_name),
                    )

                    total_pairings += 1

        conn.commit()
        await ctx.send(
            f"✅ Generated {total_pairings} pairings for season {season_number}!"
        )
        return True

    except Exception as e:
        await ctx.send(f"❌ Error generating pairings: {e}")
        return False
    finally:
        conn.close()


def add_pending_rep(reporter_id, opponent_id, reporter_result):
    conn = sqlite3.connect(sqliteFile)
    c = conn.cursor()
    c.execute(
        """INSERT INTO pending_reps (reporter_id, opponent_id, reporter_result)
                 VALUES (?, ?, ?)""",
        (reporter_id, opponent_id, reporter_result),
    )
    conn.commit()
    conn.close()


def get_pending_rep(reporter_id, opponent_id):
    conn = sqlite3.connect(sqliteFile)
    c = conn.cursor()
    cutoff_time = datetime.now() - timedelta(minutes=30)
    cutoff_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        """SELECT *
                 FROM pending_reps
                 WHERE reporter_id = ?
                   AND opponent_id = ?
                   AND timestamp >= ?
                 ORDER BY timestamp DESC LIMIT 1""",
        (reporter_id, opponent_id, cutoff_str),
    )
    rep = c.fetchone()
    conn.close()
    return rep


def update_season_game(match, game, result):
    conn = sqlite3.connect(sqliteFile)
    if game not in [1, 2]:
        return "", "wrong game number"
    c = conn.cursor()
    c.execute(
        """
            UPDATE pairings
            SET {game} = :new_result
            WHERE id = :pairing_id
        """,
        {"new_result": result, "pairing_id": match},
    )
    conn.commit()
    conn.close()


def update_match_history(match, game, result, season, pgn=""):
    print("not yet")


def get_specific_pairing(ctx, opponent, c: sqlite3.Cursor = None):

    conn = False
    if c == None:
        conn = sqlite3.connect(sqliteFile)
        c = conn.cursor()
    c.execute(
        """SELECT id, player1_id, player2_id, result1, result2
                         FROM pairings
                         WHERE ((player1_id = :playerA AND player2_id = :playerB)
                            OR (player1_id = :playerB AND player2_id = :playerA))
                            AND season_number = (SELECT season_number FROM seasons WHERE active = 1)
                            """,
        {
            "playerA": ctx.author.id,
            "playerB": opponent.id,
        },
    )
    pairing = c.fetchone()
    if conn:
        conn.close()
    return pairing
