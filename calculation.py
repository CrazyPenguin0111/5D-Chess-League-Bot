import math


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
