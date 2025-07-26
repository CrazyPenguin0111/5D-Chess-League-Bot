import csv
import math
import os
from constants import ROLES_CONFIG_FILE


def get_expected_score(a, b):
    return 1 / (1 + math.pow(10, (b - a) / 400))


def update_elo(winner_elo, loser_elo, draw=False):
    if draw:
        expected_winner = get_expected_score(winner_elo, loser_elo)
        expected_loser = get_expected_score(loser_elo, winner_elo)
        new_winner_elo = winner_elo + 25 * (0.5 - expected_winner)
        new_loser_elo = loser_elo + 25 * (0.5 - expected_loser)
        return new_winner_elo, new_loser_elo
    else:
        expected = get_expected_score(winner_elo, loser_elo)
        new_winner_elo = winner_elo + 25 * (1 - expected)
        new_loser_elo = loser_elo - 25 * (1 - expected)
        return new_winner_elo, new_loser_elo


def get_role_ranges():
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
    return role_ranges


def group_players(players, role_ranges):
    groups = {}
    for player_id, elo in players:
        for role_range in role_ranges:
            if role_range["min"] <= elo <= role_range["max"]:
                if role_range["name"] not in groups:
                    groups[role_range["name"]] = []
                groups[role_range["name"]].append(player_id)
                break
    return groups


def calculate_sb(leaderboard):
    lookup = {player["id"]: player for player in leaderboard}
    for player in leaderboard:
        for opponent_id in player["wonagainst"]:
            opponent = lookup.get(opponent_id)
            if opponent:
                player["sb"] += opponent["points"] / 2
    leaderboard.sort(key=lambda x: (x["points"], x["sb"]), reverse=True)
    return leaderboard


def calculate_match_stats(game1, game2, p1_elo, p2_elo):
    if game1 == 0.5:
        g1_p1, g1_p2 = update_elo(p1_elo, p2_elo, draw=True)
    elif game1 == 1.0:
        g1_p1, g1_p2 = update_elo(p1_elo, p2_elo)
    else:
        g1_p2, g1_p1 = update_elo(p2_elo, p1_elo)

    if game2 == 0.5:
        g2_p1, g2_p2 = update_elo(g1_p1, g1_p2, draw=True)
    elif game2 == 1.0:
        g2_p1, g2_p2 = update_elo(g1_p1, g1_p2)
    else:
        g2_p2, g2_p1 = update_elo(g1_p2, g1_p1)

    p1_wins = sum(1 for r in [game1, game2] if (r == 1.0))
    p1_losses = 2 - p1_wins - sum(1 for r in [game1, game2] if r == 0.5)
    p1_draws = sum(1 for r in [game1, game2] if r == 0.5)

    p2_wins = 2 - p1_wins - p1_draws
    p2_losses = p1_wins
    p2_draws = p1_draws
    player_1_stats = {
        "wins": p1_wins,
        "losses": p1_losses,
        "draws": p1_draws,
        "elo": g2_p1,
    }
    player_2_stats = {
        "wins": p2_wins,
        "losses": p2_losses,
        "draws": p2_draws,
        "elo": g2_p2,
    }
    return player_1_stats, player_2_stats
