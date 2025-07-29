import asyncio, sqlite3
from datetime import datetime, timedelta

import aiosqlite
from constants import (
    INITIAL_ELO,
    SQLITEFILE,
)
from logic import calculate_sb, get_role_ranges, group_players


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

        role_ranges = get_role_ranges()

        groups = group_players(players, role_ranges)

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


async def find_pairings_in_db(player_id, season, group_name):
    async with aiosqlite.connect(SQLITEFILE) as conn:
        conn.row_factory = aiosqlite.Row

        if season is None:
            cur = await conn.execute("SELECT season_number FROM seasons WHERE active=1")
            row = await cur.fetchone()
            await cur.close()
            season = row["season_number"] if row else None
            if season is None:
                raise Exception(1)

        cur = await conn.execute(
            "SELECT 1 FROM seasons WHERE season_number=?", (season,)
        )
        if not await cur.fetchone():
            await cur.close()
            raise Exception(2)
        await cur.close()

        if group_name is None:
            cur = await conn.execute(
                "SELECT group_name FROM pairings WHERE season_number=? AND (player1_id=? OR player2_id=?) LIMIT 1",
                (season, player_id, player_id),
            )
            grp = await cur.fetchone()
            await cur.close()
            if not grp:
                raise Exception(3)
            group_name = grp["group_name"]

        if group_name:

            if "procrastination" in group_name.lower() or "lazy" in group_name.lower():
                group_name = "Pro League"

            cur = await conn.execute(
                "SELECT DISTINCT group_name FROM pairings WHERE season_number=?",
                (season,),
            )
            valid = [r["group_name"].lower() for r in await cur.fetchall()]
            await cur.close()
            if group_name.lower() not in valid:
                sugg = [g for g in valid if group_name.lower() in g]
                msg = f"❌ Group '{group_name}' not found in season {season}!"
                if sugg:
                    msg += f"\nDid you mean: {', '.join(sugg[:3])}?"
                raise Exception(msg)

        if group_name:
            title += f", {group_name}"

        sql = (
            "SELECT player1_id, player2_id, result1, result2 "
            "FROM pairings WHERE season_number=?"
        )
        params = [season]
        if group_name:
            sql += " AND LOWER(group_name)=LOWER(?)"
            params.append(group_name)
        sql += " ORDER BY id"

        cur = await conn.execute(sql, params)
        pairings = await cur.fetchall()
        await cur.close()
        return pairings, season


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
    conn.close()


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


def get_specific_pairing(player_id: int, oppoent_id: int, c=None):

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
            "playerA": player_id,
            "playerB": oppoent_id,
        },
    )
    pairing = c.fetchone()

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
    conn.close()
    leaderboard = calculate_sb(leaderboard)
    return leaderboard


def get_latest_season():
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute(
        "SELECT season_number,active FROM seasons ORDER BY season_number DESC LIMIT 1"
    )
    latest_season = c.fetchone()
    conn.close()
    return latest_season


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
    conn.close()


def find_signed_players():
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("SELECT id, elo FROM players WHERE signed_up=1")
    players = c.fetchall()
    conn.close()
    return players


def update_missed_seasons():
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("UPDATE players SET seasons_missed = 0 WHERE signed_up = 1")
    c.execute(
        "UPDATE players SET seasons_missed = seasons_missed + 1 WHERE signed_up = 0"
    )
    conn.commit()
    conn.close()


def find_unsigned_players():
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("SELECT id, elo, seasons_missed FROM players WHERE signed_up=0")
    n_players = c.fetchall()
    conn.close()
    return n_players


def punish_player(player_id, elo, missed_seasons):
    conn = sqlite3.connect()
    c = conn.cursor()
    if missed_seasons > 1:
        if elo - 1380 > 10:
            elo -= 10
        else:
            elo = 1380

        c.execute("UPDATE players SET elo = ? WHERE id = ?", (elo, player_id))
        conn.commit()
    conn.close()


def setup_future_season(old_season, new_season):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("UPDATE players SET signed_up=0")
    c.execute(
        "INSERT INTO seasons (season_number, active) VALUES (?, 0)", (new_season,)
    )

    c.execute("UPDATE seasons SET active=0 WHERE season_number=?", (old_season,))
    conn.commit()
    conn.close()


def activate_season(current_season):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("UPDATE seasons SET active=1 WHERE season_number=?", (current_season,))
    conn.commit()
    conn.close()


async def bundle_leaderboard(player_id, limit, member_ids):
    async with aiosqlite.connect(SQLITEFILE) as conn:
        conn.row_factory = aiosqlite.Row

        # 3a. total players (for “of X” in footer)
        if member_ids:
            q_total = f"SELECT COUNT(*) as cnt FROM players WHERE id IN ({','.join('?'*len(member_ids))})"
            cur = await conn.execute(q_total, member_ids)
        else:
            cur = await conn.execute("SELECT COUNT(*) as cnt FROM players")
        row = await cur.fetchone()
        await cur.close()
        total_players = row["cnt"]

        # 3b. find your own ELO & rank
        cur = await conn.execute(
            "SELECT elo, wins, losses, draws FROM players WHERE id=?", (player_id,)
        )
        you = await cur.fetchone()
        await cur.close()

        user_rank = None
        surrounding = []
        if you:
            your_elo = you["elo"]
            # count how many have strictly higher ELO
            if member_ids:
                q_rank = (
                    f"SELECT COUNT(*) as cnt FROM players WHERE elo>? "
                    f"AND id IN ({','.join('?'*len(member_ids))})"
                )
                params = (your_elo, *member_ids)
            else:
                q_rank = "SELECT COUNT(*) as cnt FROM players WHERE elo>?"
                params = (your_elo,)
            cur = await conn.execute(q_rank, params)
            user_rank = (await cur.fetchone())["cnt"] + 1
            await cur.close()

        # 3c. fetch leaderboard rows
        rows = []
        base_query = "SELECT id, elo, wins, losses, draws FROM players"
        where = ""
        params = ()
        if member_ids:
            where = f" WHERE id IN ({','.join('?'*len(member_ids))})"
            params = tuple(member_ids)
        order = " ORDER BY elo DESC"

        if you and user_rank and user_rank > limit:
            #  top N + your surrounding 3
            top_q = base_query + where + order + " LIMIT ?"
            cur = await conn.execute(top_q, params + (limit,))
            top = await cur.fetchall()
            await cur.close()

            off = max(0, user_rank - 2)
            surround_q = base_query + where + order + " LIMIT 3 OFFSET ?"
            cur = await conn.execute(surround_q, params + (off,))
            surrounding = await cur.fetchall()
            await cur.close()
            rows = top
        else:
            # user is in top N or not registered → just top N
            top_q = base_query + where + order + " LIMIT ?"
            cur = await conn.execute(top_q, params + (limit,))
            rows = await cur.fetchall()
            await cur.close()
    return total_players, you, user_rank, surrounding, rows


def add_and_resolve_report(author_id, opponent_id, game_number, result):
    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    new_rep = False

    def find_gameresults_in_db(inner_p1_id, inner_p2_id):
        c.execute(
            """SELECT result1, result2 FROM pairings WHERE (player1_id = ? AND player2_id = ?) AND season_number = (SELECT season_number FROM seasons WHERE active = 1)""",
            (inner_p1_id, inner_p2_id),
        )
        return c.fetchone()

    season_active = c.execute(
        "SELECT active FROM seasons ORDER BY season_number DESC LIMIT 1"
    ).fetchone()[0]
    if season_active:
        pairing = get_specific_pairing(author_id, opponent_id)
        if not pairing:
            raise Exception(1)
        pairing_id, p1_id, p2_id, _, _ = pairing
        is_player1 = author_id == p1_id
        result_value = (
            1.0
            if (result == "w" and is_player1) or (result == "l" and not is_player1)
            else 0.0
        )
        if result == "d":
            result_value = 0.5

        c.execute(
            """SELECT reporter_id, result
                        FROM pending_reps
                        WHERE pairing_id = ?
                        AND game_number = ?
            """,
            (pairing_id, game_number),
        )
        existing_rep = c.fetchone()

        game1, game2 = find_gameresults_in_db(p1_id, p2_id)
        if game_number == 1:
            if game1 is not None:
                raise Exception(2)
        if game_number == 2:
            if game2 is not None:
                raise Exception(2)
        if existing_rep:
            if existing_rep[0] == opponent_id:
                expected_result = {"w": "l", "l": "w", "d": "d"}[existing_rep[1]]
                if result != expected_result:
                    raise Exception(3)

                c.execute(
                    f"""UPDATE pairings 
                                 SET result{game_number}=?
                                 WHERE id=?""",
                    (result_value, pairing_id),
                )
                conn.commit()
                update_match_history(
                    pairing_id,
                    game_number,
                    result_value,
                )
                c.execute("DELETE FROM pending_reps WHERE pairing_id=?", (pairing_id,))
                conn.commit()
            else:
                raise Exception(4)
        else:
            c.execute(
                """INSERT INTO pending_reps
                                (pairing_id, reporter_id, result, game_number)
                            VALUES (?, ?, ?, ?)""",
                (pairing_id, author_id, result, game_number),
            )
            conn.commit()
            new_rep = True

    game1, game2 = find_gameresults_in_db(p1_id, p2_id)
    conn.close()
    return game1, game2, p1_id, p2_id, new_rep
