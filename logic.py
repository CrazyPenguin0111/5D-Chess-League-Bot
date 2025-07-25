import csv
import math

from constants import ROLES_CONFIG_FILE
import os


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
