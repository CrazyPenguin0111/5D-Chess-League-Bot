import asyncio, sqlite3
from datetime import datetime, timedelta
from constants import (
    INITIAL_ELO,
    SQLITEFILE,
)
from logic import get_role_ranges, group_players


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
