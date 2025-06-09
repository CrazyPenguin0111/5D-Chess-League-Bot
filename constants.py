ROLES_CONFIG_FILE = "elo_roles.csv"
SQLITEFILE = "elo_bot.db"
K_FACTOR = 25
INITIAL_ELO = 1380

DATABASE_STRUCTURE = {
    "pairings": [
        "id",
        "player1_id",
        "player2_id",
        "result1",
        "result2",
        "season_number",
        "group_name",
    ],
    "pending_reps": [
        "id",
        "pairing_id",
        "reporter_id",
        "result",
        "game_number",
        "timestamp",
    ],
    "players": ["id", "elo", "wins", "losses", "draws", "signed_up"],
    "seasons": ["season_number", "active"],
    "match_history": [
        "match",
        "whiteplayer",
        "blackplayer",
        "colorwon",
        "season",
        "league",
    ],
    "elo_history": ["id", "player_id", "elo_change", "timestamp"],
    "player_aliases": ["id", "player_id", "alias"],
}

DATABASE_STRUCTURE_CREATIONSTRINGMAPPING = {
    "Tables": {
        "players": "id INTEGER PRIMARY KEY",
        "pending_reps": "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "seasons": "season_number INTEGER PRIMARY KEY",
        "pairings": "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "match_history": "match INTEGER PRIMARY KEY AUTOINCREMENT",
        "elo_history": "id INTEGER PRIMARY KEY AUTOINCREMENT",
        "player_aliases": "id INTEGER PRIMARY KEY AUTOINCREMENT",
    },
    "players": {
        "elo": "REAL DEFAULT 1380",
        "wins": "INTEGER DEFAULT 0",
        "losses": "INTEGER DEFAULT 0",
        "draws": "INTEGER DEFAULT 0",
        "signed_up": "INTEGER DEFAULT 0",
    },
    "pairings": {
        "player1_id": "INTEGER",
        "player2_id": "INTEGER",
        "result1": "REAL DEFAULT null",
        "result2": "REAL DEFAULT null",
        "season_number": "INTEGER",
        "group_name": "TEXT",
        "foreignkeyconstraint": """
                FOREIGN KEY
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
                )""",
    },
    "pending_reps": {
        "pairing_id": "INTEGER",
        "reporter_id": "INTEGER",
        "result": "TEXT",
        "game_number": "INTEGER",
        "timestamp": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        "foreignkeyconstraint": """
                FOREIGN KEY
                (
                    pairing_id
                ) REFERENCES pairings
                (
                    id
                )
                FOREIGN KEY
                (
                    reporter_id
                ) REFERENCES players
                (
                    id
                )
                """,
    },
    "seasons": {"active": "INTEGER DEFAULT 0"},
    "match_history": {
        "whiteplayer": "INTEGER",
        "blackplayer": "INTEGER",
        "colorwon": "TEXT",
        "season": "TEXT",
        "league": "TEXT",
        "foreignkeyconstraint": """
                FOREIGN KEY
                (
                    whiteplayer
                ) REFERENCES players
                (
                    id
                ),
                FOREIGN KEY
                (
                    blackplayer
                ) REFERENCES players
                (
                    id
                )""",
    },
    "elo_history": {
        "player_id": "INTEGER",
        "elo_change": "INTEGER",
        "timestamp": "DATETIME DEFAULT CURRENT_TIMESTAMP",
        "foreignkeyconstraint": """
                FOREIGN KEY
                (
                    player_id
                ) REFERENCES players
                (
                    id
                )""",
    },
    "player_aliases": {
        "player_id": "INTEGER",
        "alias": "TEXT UNIQUE",
        "foreignkeyconstraint": """
                FOREIGN KEY
                (
                    player_id
                ) REFERENCES players
                (
                    id
                )""",
    },
}
