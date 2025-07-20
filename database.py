import asyncio, csv, os, sqlite3, shutil
from datetime import datetime, timedelta
from constants import *
from constants import SQLITEFILE


def init_db():
    missing, extra, wrong_type = check_database_structure(SQLITEFILE)

    reduced_missing, reduced_extra = reduce(missing), reduce(extra)
    if not os.path.exists("backup"):
        os.makedirs("backup")
    if len(missing) + len(extra) > 0:
        repair_db(reduced_missing, reduced_extra)
    if len(wrong_type) > 0:
        print(wrong_type)
        if input(
            "wrong prefered column types detected do you want to repair typestructure(yes/NO):"
        ).lower() in ["y", "yes"]:
            conn = sqlite3.connect(SQLITEFILE)
            shutil.copy(SQLITEFILE, f"backup/{datetime.now().timestamp()}_{SQLITEFILE}")
            c = conn.cursor()
            for table in list({entry["table"] for entry in wrong_type}):
                c.execute(f"ALTER TABLE {table} RENAME TO old_{table}")
                conn.commit()
                c.execute(build_table_string(table))
                conn.commit()
                c.execute(f"INSERT INTO {table} SELECT * from old_{table}")
                conn.commit()
                c.execute(f"DROP TABLE old_{table}")
                conn.commit()
            conn.close()


def repair_db(reduced_missing, reduced_extra):
    conn = sqlite3.connect(SQLITEFILE)
    shutil.copy(SQLITEFILE, f"backup/{datetime.now().timestamp()}_{SQLITEFILE}")
    for missed in reduced_missing:
        c = conn.cursor()
        table = missed["table"]
        match missed["type"]:
            case "table":
                sqlstring = build_table_string(table)
                c.execute(sqlstring)
                conn.commit()
                print(f"added {table}")
            case "column":
                col = missed["column"]
                sqlstring = f"ALTER TABLE {table} ADD COLUMN {col} {DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table][col]};"
                c.execute(sqlstring)
                conn.commit()
                print(f"added {col} to {table}")

    for ext in reduced_extra:
        c = conn.cursor()
        table = ext["table"]
        match ext["type"]:
            case "table":
                sqlstring = f"DROP TABLE {table};"
                c.execute(sqlstring)
                conn.commit()
                print(f"dropped {table}")
            case "column":
                col = ext["column"]
                sqlstring = f"ALTER TABLE {table} DROP COLUMN {col};"
                c.execute(sqlstring)
                conn.commit
                print(f"dropped {col} in {table}")
    conn.commit()
    conn.close()


def build_table_string(table):
    sqlstring = f"CREATE TABLE {table} ({DATABASE_STRUCTURE_CREATIONSTRINGMAPPING['Tables'][table]}"
    items = DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table].items()
    for coltitle, coltype in items:
        if coltitle == "foreignkeyconstraint":
            sqlstring += f", {coltype}"
            continue
        sqlstring += f", {coltitle} {coltype}"
    sqlstring += ");"
    return sqlstring


def reduce(list):
    skip, reduced_list = [], []
    for missed in list:
        if missed["type"] == "table":
            skip.append(missed["table"])
        elif missed["table"] in skip:
            continue
        reduced_list.append(missed)

    return reduced_list


def delete_pending_rep(rep_id):

    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("DELETE FROM pending_reps WHERE id=?", (rep_id,))
    conn.commit()
    conn.close()


def update_player_stats(player_id, elo, wins=0, losses=0, draws=0):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute(
        "INSERT INTO elo_history (player_id, elo_change) VALUES (?,?)",
        (player_id, elo),
    )
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
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE id=?", (player_id,))
    player = c.fetchone()
    conn.close()
    return player


async def clean_old_pending_matches():
    while True:
        try:
            conn = sqlite3.connect(SQLITEFILE)
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
        conn = sqlite3.connect(SQLITEFILE)
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
                cnt = len(player_ids)
                result = []
                while cnt > 12:
                    cnt -= 6
                    result.append(6)

                remainder = cnt // 2
                result.append(remainder)
                result.append(cnt - remainder)

                for i in range(len(result)):
                    for j in range(0, len(result)):
                        if i != j:
                            while result[i] > result[j]:
                                result[j] += 1
                                result[i] -= 1

                nolook = 0
                for size in result:
                    subgroups.append(player_ids[nolook : nolook + size])
                    nolook += size

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
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute(
        """INSERT INTO pending_reps (reporter_id, opponent_id, reporter_result)
                 VALUES (?, ?, ?)""",
        (reporter_id, opponent_id, reporter_result),
    )
    conn.commit()
    conn.close()


def get_pending_rep(reporter_id, pairing_id):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    cutoff_time = datetime.now() - timedelta(minutes=30)
    cutoff_str = cutoff_time.strftime("%Y-%m-%d %H:%M:%S")
    c.execute(
        """SELECT *
                 FROM pending_reps
                 WHERE reporter_id = ?
                   AND pairing_id = ?
                   AND timestamp >= ?
                 ORDER BY timestamp DESC LIMIT 1""",
        (reporter_id, pairing_id, cutoff_str),
    )
    rep = c.fetchone()
    conn.close()
    return rep


def update_season_game(match, game, result):
    conn = sqlite3.connect(SQLITEFILE)
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


def update_match_history(match, game, result):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute(
        """
              SELECT player1_id,player2_id,season_number,group_name
              FROM pairings
              WHERE id = ?
              """,
        (match,),
    )

    mapping = {1.0: "w", 0.0: "b", 0.5: "d"}

    if game == 1:
        whitePlayer, blackPlayer, season, league = c.fetchone()
        data = {
            "white": whitePlayer,
            "black": blackPlayer,
            "result": mapping[round(result, 1)],
            "season": season,
            "league": league,
        }
    else:
        blackPlayer, whitePlayer, season, league = c.fetchone()
        data = {
            "white": whitePlayer,
            "black": blackPlayer,
            "result": mapping[round(1 - result, 1)],
            "season": season,
            "league": league,
        }
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO match_history (whiteplayer, blackplayer, colorwon, season, league)
        VALUES (:white, :black, :result, :season, :league)
        """,
        data,
    )
    conn.commit()


def find_player_group(player_id, season):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute(
        """
        SELECT group_name FROM pairings where (player1_id = :player or player2_id = :player) and season_number = :season;
        """,
        {"season": season, "player": player_id},
    )
    group = c.fetchone()
    if not group:
        c.execute(
            """
            SELECT league from match_history where (whiteplayer = :player or blackplayer = :player) and REPLACE(UPPER(season), 'SEASON ', '') = :season
            """,
            {"season": season, "player": player_id},
        )
        group = c.fetchone()
    if not group:
        group = ("",)
    return group[0]


def get_specific_pairing(ctx, opponent, c=None):

    conn = False
    if c == None:
        conn = sqlite3.connect(SQLITEFILE)
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


def get_group_ranking(season, group):
    def players_activeseason(p):
        return f"SELECT DISTINCT(player{p}_id) FROM pairings WHERE group_name = REPLACE(:group, '-B', '-2') or group_name = REPLACE(:group, '-A', '-1') or group_name = REPLACE(:group, '-c', '-3') or group_name = REPLACE(:group, '-D', '-4')"

    def players_historicseason(p):
        return f"""
            SELECT DISTINCT({p}player)
            FROM match_history
            WHERE REPLACE(UPPER(season), 'SEASON ', '') = :season
            AND (league = :group OR league = REPLACE(:group, '-A', '-1') OR league = REPLACE(:group, '-B', '-2') OR league = REPLACE(:group, '-C', '-3') OR league = REPLACE(:group, '-D', '-4'))
        """

    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("SELECT active FROM seasons WHERE season_number = ?", (season,))
    if c.fetchone():
        c.execute(players_activeseason(1), {"group": group})
        playerlist = {player[0] for player in c.fetchall()}
        c.execute(players_activeseason(2), {"group": group})
        playerlist.update({player[0] for player in c.fetchall()})
    else:
        sqlObj = {"season": season, "group": group}
        c.execute(players_historicseason("white"), sqlObj)
        playerlist = {player[0] for player in c.fetchall()}
        c.execute(players_historicseason("black"), sqlObj)
        playerlist.update({player[0] for player in c.fetchall()})
    leaderboard = []
    for player in playerlist:
        c.execute(
            """SELECT
                    SUM(
                        CASE 
                            WHEN colorwon = 'w' AND whiteplayer = :player THEN 1
                            WHEN colorwon = 'b' AND blackplayer = :player THEN 1
                            WHEN colorwon = 'd' AND (blackplayer = :player OR whiteplayer = :player) THEN 0.5
                            ELSE 0
                        END
                    ) AS total_points
                FROM match_history
                WHERE REPLACE(UPPER(season), 'SEASON ', '') = :season;
            """,
            {"player": player, "season": str(season)},
        )

        points = c.fetchone()[0]
        c.execute(
            """SELECT opponent_id
                FROM (
                    SELECT blackplayer AS opponent_id
                    FROM match_history
                    WHERE colorwon = 'w'
                    AND whiteplayer = :player
                    AND REPLACE(UPPER(season), 'SEASON ', '') = :season

                    UNION ALL

                    SELECT whiteplayer AS opponent_id
                    FROM match_history
                    WHERE colorwon = 'b'
                    AND blackplayer = :player
                    AND REPLACE(UPPER(season), 'SEASON ', '') = :season
                )
            """,
            {"player": player, "season": str(season)},
        )
        if points == None:
            points = 0
        wonagainstlist = [opponent[0] for opponent in c.fetchall()]
        leaderboard.append(
            {"id": player, "points": points, "wonagainst": wonagainstlist, "sb": 0}
        )
    lookup = {player["id"]: player for player in leaderboard}
    for player in leaderboard:
        for opponent_id in player["wonagainst"]:
            opponent = lookup.get(opponent_id)
            if opponent:
                player["sb"] += opponent["points"] / 2
    leaderboard.sort(key=lambda x: (x["points"], x["sb"]), reverse=True)
    return leaderboard


def get_latest_season():
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute(
        "SELECT season_number,active FROM seasons ORDER BY season_number DESC LIMIT 1"
    )
    return c.fetchone()


def check_database_structure(db_file):

    try:
        conn = sqlite3.connect(db_file)
        c = conn.cursor()

        c.execute("SELECT name FROM sqlite_master WHERE type='table';")
        actual_tables = {row[0] for row in c.fetchall()}
        missing = []
        for table, expected_columns in DATABASE_STRUCTURE.items():
            if table not in actual_tables:
                print(f"Missing table: {table}")
                missing.append({"type": "table", "table": table})
                for col in expected_columns:
                    missing.append({"type": "column", "table": table, "column": col})
                continue

            c.execute(f"PRAGMA table_info({table});")
            actual_columns = {row[1] for row in c.fetchall()}
            for col in expected_columns:
                if col not in actual_columns:
                    print(f"Missing column in {table}: {col}")
                    missing.append({"type": "column", "table": table, "column": col})
        extra, wrong_type = [], []
        for table in actual_tables:
            if table == "sqlite_sequence":
                continue
            if table not in DATABASE_STRUCTURE:
                extra.append({"type": "table", "table": table})
                print(f"Extra table: {table}")
                c.execute(f"PRAGMA table_info({table});")
                table_info = c.fetchall()
                actual_columns = {row[1] for row in table_info}
                for col in actual_columns:
                    extra.append({"type": "column", "table": table, "column": col})
            elif table in DATABASE_STRUCTURE:
                expected_columns = DATABASE_STRUCTURE[table]
                c.execute(f"PRAGMA table_info({table});")
                table_info = c.fetchall()
                actual_columns = {row[1]: row[2] for row in table_info}
                for col, type in actual_columns.items():
                    if col not in expected_columns:
                        print(f"Extra column in {table}: {col}")
                        extra.append({"type": "column", "table": table, "column": col})
                    else:
                        colmap = DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table]

                        if col not in colmap:
                            continue
                        expected_type = DATABASE_STRUCTURE_CREATIONSTRINGMAPPING[table][
                            col
                        ].split(" ")[0]
                        if type.upper() != expected_type.upper():
                            wrong_type.append(
                                {
                                    "table": table,
                                    "column": col,
                                    "type": type,
                                    "expected_type": expected_type,
                                }
                            )

        return missing, extra, wrong_type
    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        return False
    finally:
        conn.close()


def register_new_player(player_id):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("INSERT INTO players (id, elo) VALUES (?, ?)", (player_id, INITIAL_ELO))
    conn.commit()
    conn.close()


def sign_up_player(player_id):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("UPDATE players SET signed_up=1 WHERE id=?", (player_id,))
    conn.commit()
