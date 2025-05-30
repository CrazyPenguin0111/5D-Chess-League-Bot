import discord
from discord.ext import commands, tasks
import sqlite3
import math
import csv
import os
import asyncio
from datetime import datetime, timedelta


# Configuration loader
def load_config():
    config = {}
    try:
        with open('config.csv', mode='r') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                config[row['setting']] = row['value']

        if 'token' not in config or not config['token']:
            raise ValueError("Bot token not found in config.csv")
        if 'channel_id' not in config or not config['channel_id']:
            raise ValueError("Channel ID not found in config.csv")

        return config
    except FileNotFoundError:
        raise FileNotFoundError("config.csv file not found")
    except Exception as e:
        raise Exception(f"Error reading config.csv: {str(e)}")


# Load configuration
try:
    config = load_config()
    BOT_TOKEN = config['token']
    ALLOWED_CHANNEL_ID = int(config['channel_id'])
except Exception as e:
    print(f"Configuration error: {e}")
    exit(1)

# Constants
K_FACTOR = 25
INITIAL_ELO = 1380
ROLES_CONFIG_FILE = 'elo_roles.csv'

# Initialize bot
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='$', intents=intents, help_command=None)


# Database functions
def init_db():
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()

    # Players table with signed_up column
    c.execute('''CREATE TABLE IF NOT EXISTS players
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
                 )''')

    # Modify pending_reps table for season matches
    c.execute('''CREATE TABLE IF NOT EXISTS pending_reps
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
                 )''')

    # Seasons table
    c.execute('''CREATE TABLE IF NOT EXISTS seasons
                 (
                     season_number
                     INTEGER
                     PRIMARY
                     KEY,
                     active
                     INTEGER
                     DEFAULT
                     0
                 )''')

    # Pairings table with both result columns
    c.execute('''CREATE TABLE IF NOT EXISTS pairings
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
        )''')

    # Initialize season 1 if it doesn't exist
    c.execute('''INSERT
    OR IGNORE INTO seasons (season_number, active) VALUES (1, 0)''')

    # Check and add missing columns to pairings table
    c.execute("PRAGMA table_info(pairings)")
    columns = [col[1] for col in c.fetchall()]

    if 'result1' not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN result1 REAL DEFAULT NULL")
    if 'result2' not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN result2 REAL DEFAULT NULL")
    if 'season_number' not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN season_number INTEGER")
    if 'group_name' not in columns:
        c.execute("ALTER TABLE pairings ADD COLUMN group_name TEXT")

    # Add signed_up column if it doesn't exist in players table
    if 'signed_up' not in [col[1] for col in c.execute("PRAGMA table_info(players)").fetchall()]:
        c.execute("ALTER TABLE players ADD COLUMN signed_up INTEGER DEFAULT 0")

    conn.commit()
    conn.close()

init_db()


# ELO calculations
def get_expected_score(a, b):
    return 1 / (1 + math.pow(10, (b - a) / 400))


def update_elo(winner_elo, loser_elo, draw=False):
    if draw:
        expected_winner = get_expected_score(winner_elo, loser_elo)
        expected_loser = get_expected_score(loser_elo, winner_elo)
        new_winner_elo = winner_elo + K_FACTOR * (0.5 - expected_winner)
        new_loser_elo = loser_elo + K_FACTOR * (0.5 - expected_loser)
        return new_winner_elo, new_loser_elo
    else:
        expected = get_expected_score(winner_elo, loser_elo)
        new_winner_elo = winner_elo + K_FACTOR * (1 - expected)
        new_loser_elo = loser_elo + K_FACTOR * (0 - (1 - expected))
        return new_winner_elo, new_loser_elo


# Player management
def get_player_data(player_id):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE id=?", (player_id,))
    player = c.fetchone()
    conn.close()
    return player


async def generate_pairings(ctx, season_number):
    """Generate pairings for the season"""
    try:
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()

        # Get all signed up players with their ELO
        c.execute('''SELECT id, elo
                     FROM players
                     WHERE signed_up = 1''')
        players = c.fetchall()

        if not players:
            await ctx.send("❌ No players have signed up for the season!")
            return False

        # Load role ranges
        role_ranges = []
        if os.path.exists(ROLES_CONFIG_FILE):
            with open(ROLES_CONFIG_FILE, mode='r') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    if not row.get('role') or not row.get('min elo') or not row.get('max elo'):
                        continue
                    try:
                        role_ranges.append({
                            'name': row['role'].strip(),
                            'min': int(row['min elo']),
                            'max': int(row['max elo'])
                        })
                    except ValueError:
                        continue

        # Sort by min ELO (highest first)
        role_ranges.sort(key=lambda x: x['min'], reverse=True)

        # Group players by their league role
        groups = {}
        for player_id, elo in players:
            # Find appropriate role
            for role_range in role_ranges:
                if role_range['min'] <= elo <= role_range['max']:
                    if role_range['name'] not in groups:
                        groups[role_range['name']] = []
                    groups[role_range['name']].append(player_id)
                    break

        if not groups:
            await ctx.send("❌ Couldn't group players by league roles!")
            return False

        # Generate pairings for each group
        total_pairings = 0
        for group_name, player_ids in groups.items():
            # Split into subgroups if needed
            subgroups = []
            if len(player_ids) > 7:
                # Randomize and split into subgroups of 4-7 players
                import random
                random.shuffle(player_ids)
                subgroup_size = max(4, len(player_ids) // ((len(player_ids) // 7) + 1))
                subgroups = [player_ids[i:i + subgroup_size] for i in range(0, len(player_ids), subgroup_size)]
            else:
                subgroups = [player_ids]

            # Generate round robin pairings for each subgroup
            for i, subgroup in enumerate(subgroups):
                subgroup_name = group_name if len(subgroups) == 1 else f"{group_name}-{i + 1}"

                # Generate all possible unique pairings
                from itertools import combinations
                pairings = list(combinations(subgroup, 2))

                # Insert pairings into database (two games per pairing)
                for p1, p2 in pairings:
                    # Game 1: p1 vs p2
                    c.execute('''INSERT INTO pairings
                                     (player1_id, player2_id, season_number, group_name)
                                 VALUES (?, ?, ?, ?)''',
                              (p1, p2, season_number, subgroup_name))

                    total_pairings += 1

        conn.commit()
        await ctx.send(f"✅ Generated {total_pairings} pairings for season {season_number}!")
        return True

    except Exception as e:
        await ctx.send(f"❌ Error generating pairings: {e}")
        return False
    finally:
        conn.close()

@bot.command(name='update_roles')
@commands.has_permissions(manage_roles=True)
async def update_player_roles(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:
        # Check if file exists
        if not os.path.exists(ROLES_CONFIG_FILE):
            raise FileNotFoundError(f"'{ROLES_CONFIG_FILE}' not found in bot directory")

        # Load and validate role configuration
        role_ranges = []
        with open(ROLES_CONFIG_FILE, mode='r') as csvfile:
            reader = csv.DictReader(csvfile)
            if not reader.fieldnames or 'role' not in reader.fieldnames or 'min elo' not in reader.fieldnames or 'max elo' not in reader.fieldnames:
                raise ValueError("CSV file must have headers: 'role', 'min elo', 'max elo'")

            for row in reader:
                if not row.get('role') or not row.get('min elo') or not row.get('max elo'):
                    continue

                try:
                    role_ranges.append({
                        'name': row['role'].strip(),
                        'min': int(row['min elo']),
                        'max': int(row['max elo'])
                    })
                except ValueError:
                    raise ValueError(f"Invalid ELO values in row: {row}")

        if not role_ranges:
            raise ValueError("No valid role ranges found in the configuration file")

        # Sort by min ELO (highest first)
        role_ranges.sort(key=lambda x: x['min'], reverse=True)

        # Get all registered players who are signed up
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()
        c.execute("SELECT id, elo FROM players WHERE signed_up=1")
        players = c.fetchall()
        conn.close()

        if not players:
            await ctx.send("No signed up players found!")
            return

        # Process each player
        updated_count = 0
        progress_msg = await ctx.send("Updating roles... 0%")

        for i, (player_id, elo) in enumerate(players):
            try:
                member = await ctx.guild.fetch_member(player_id)
                if not member:
                    continue

                # Find appropriate role
                new_role = None
                for role_range in role_ranges:
                    if role_range['min'] <= elo <= role_range['max']:
                        new_role = discord.utils.get(ctx.guild.roles, name=role_range['name'])
                        if not new_role:
                            await ctx.send(f"⚠️ Role '{role_range['name']}' not found on server!")
                            continue
                        break

                if not new_role:
                    continue

                # Remove all existing league roles
                roles_to_remove = []
                for role_range in role_ranges:
                    existing_role = discord.utils.get(member.roles, name=role_range['name'])
                    if existing_role:
                        roles_to_remove.append(existing_role)

                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)

                # Add new role
                await member.add_roles(new_role)
                updated_count += 1

                # Update progress every 10% or every 5 players (whichever is larger)
                if (i + 1) % max(5, len(players) // 10) == 0:
                    progress = int((i + 1) / len(players) * 100)
                    await progress_msg.edit(content=f"Updating roles... {progress}%")

            except discord.Forbidden:
                await ctx.send("❌ Bot doesn't have permission to manage roles!")
                return
            except discord.HTTPException as e:
                print(f"HTTP Error updating {player_id}: {e}")
            except Exception as e:
                print(f"Error updating {player_id}: {e}")

        await progress_msg.delete()
        await ctx.send(f"✅ Successfully updated roles for {updated_count}/{len(players)} signed up players!")

    except FileNotFoundError as e:
        await ctx.send(f"❌ {e}\nPlease create a '{ROLES_CONFIG_FILE}' file with columns: 'role', 'min elo', 'max elo'")
    except ValueError as e:
        await ctx.send(f"❌ Invalid configuration: {e}")
    except Exception as e:
        await ctx.send(f"❌ Unexpected error: {e}")

def add_pending_rep(reporter_id, opponent_id, reporter_result):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO pending_reps (reporter_id, opponent_id, reporter_result)
                 VALUES (?, ?, ?)''',
              (reporter_id, opponent_id, reporter_result))
    conn.commit()
    conn.close()


def get_pending_rep(reporter_id, opponent_id):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    cutoff_time = datetime.now() - timedelta(minutes=30)
    cutoff_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''SELECT *
                 FROM pending_reps
                 WHERE reporter_id = ?
                   AND opponent_id = ?
                   AND timestamp >= ?
                 ORDER BY timestamp DESC LIMIT 1''',
              (reporter_id, opponent_id, cutoff_str))
    rep = c.fetchone()
    conn.close()
    return rep

def update_player_stats(player_id, elo, wins=0, losses=0, draws=0):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    c.execute('''UPDATE players
                 SET elo=?,
                     wins=wins + ?,
                     losses=losses + ?,
                     draws=draws + ?
                 WHERE id = ?''',
              (elo, wins, losses, draws, player_id))
    conn.commit()
    conn.close()

def delete_pending_rep(rep_id):
    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    c.execute("DELETE FROM pending_reps WHERE id=?", (rep_id,))
    conn.commit()
    conn.close()


# Background tasks
async def clean_old_pending_matches():
    while True:
        try:
            conn = sqlite3.connect('elo_bot.db')
            c = conn.cursor()
            cutoff_time = datetime.now() - timedelta(minutes=30)
            cutoff_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S')
            c.execute("DELETE FROM pending_reps WHERE timestamp < ?", (cutoff_str,))
            deleted_count = c.rowcount
            conn.commit()
            if deleted_count > 0:
                print(f"Cleaned up {deleted_count} old pending matches")
            conn.close()
        except Exception as e:
            print(f"Error cleaning pending matches: {e}")
        await asyncio.sleep(1800)  # 30 minutes


# Channel check
def check_channel(ctx):
    if ctx.channel.id != ALLOWED_CHANNEL_ID:
        allowed_channel = bot.get_channel(ALLOWED_CHANNEL_ID)
        channel_name = f"#{allowed_channel.name}" if allowed_channel else f"channel with ID {ALLOWED_CHANNEL_ID}"
        return False, f"This command can only be used in {channel_name}!"
    return True, None


# Commands
@bot.command(name='register')
async def register_player(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    player_id = ctx.author.id
    if get_player_data(player_id):
        await ctx.send(f"{ctx.author.mention}, you're already registered!")
        return

    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()
    c.execute("INSERT INTO players (id, elo) VALUES (?, ?)", (player_id, INITIAL_ELO))
    conn.commit()
    conn.close()

    await ctx.send(f"🎉 {ctx.author.mention} has been registered with an initial ELO of {INITIAL_ELO}!")


@bot.command(name='rep')
async def report_match(ctx, result: str, opponent: discord.Member, game_number: int = None):
    """Report or confirm match results and update all relevant data"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    # Validate inputs
    result = result.lower()
    if result not in ['w', 'l', 'd']:
        await ctx.send("❌ Invalid result. Use 'w', 'l', or 'd'.")
        return

    if ctx.author.id == opponent.id:
        await ctx.send("❌ You can't report a match with yourself!")
        return

    # Check registrations
    reporter_data = get_player_data(ctx.author.id)
    opponent_data = get_player_data(opponent.id)
    if not reporter_data or not opponent_data:
        await ctx.send("❌ Both players must be registered!")
        return

    conn = sqlite3.connect('elo_bot.db')
    try:
        c = conn.cursor()
        season_active = c.execute("SELECT active FROM seasons ORDER BY season_number DESC LIMIT 1").fetchone()[0]

        if season_active and game_number is not None:
            # ====== Season Match Handling ======
            if game_number not in [1, 2]:
                await ctx.send("❌ Invalid game number. Use 1 or 2.")
                return

            # Find the specific game pairing
            c.execute('''SELECT id, player1_id, player2_id, result1, result2
                         FROM pairings
                         WHERE ((player1_id = ? AND player2_id = ?)
                             OR (player1_id = ? AND player2_id = ?))
                           AND season_number = (SELECT season_number FROM seasons WHERE active = 1)
                           AND game_number = ?''',
                      (ctx.author.id, opponent.id, opponent.id, ctx.author.id, game_number))
            pairing = c.fetchone()

            if not pairing:
                await ctx.send("❌ No valid season pairing found!")
                return

            pairing_id, p1_id, p2_id, existing_result1, existing_result2 = pairing

            # Determine perspective
            is_player1 = ctx.author.id == p1_id
            result_value = 1.0 if (result == 'w' and is_player1) or (result == 'l' and not is_player1) else 0.0
            if result == 'd':
                result_value = 0.5

            # Check existing pending report
            c.execute('''SELECT reporter_id, result
                         FROM pending_reps
                         WHERE pairing_id = ?
                           AND game_number = ?''',
                      (pairing_id, game_number))
            existing_rep = c.fetchone()

            if existing_rep:
                # Handle confirmation
                if existing_rep[0] == opponent.id:
                    expected_result = {'w': 'l', 'l': 'w', 'd': 'd'}[existing_rep[1]]
                    if result != expected_result:
                        await ctx.send("❌ Results don't match! Please report the opposite result.")
                        return

                    # Update pairings table for specific game
                    c.execute(f'''UPDATE pairings 
                                 SET result{game_number}=?
                                 WHERE id=? AND game_number=?''',
                              (result_value, pairing_id, game_number))
                    conn.commit()

                    # Check if both games are completed
                    c.execute('''SELECT result1, result2
                                 FROM pairings
                                 WHERE (player1_id = ? AND player2_id = ?)
                                   AND season_number = (SELECT season_number FROM seasons WHERE active = 1)
                                 ORDER BY game_number''', (p1_id, p2_id))
                    game_results = c.fetchall()

                    if len(game_results) == 2 and all(r is not None for res in game_results for r in res):
                        game1, game2 = game_results[0][0], game_results[1][0]

                        # Calculate ELO changes
                        p1_elo = get_player_data(p1_id)[1]
                        p2_elo = get_player_data(p2_id)[1]

                        # Game 1 calculation
                        if game1 == 0.5:
                            g1_p1, g1_p2 = update_elo(p1_elo, p2_elo, draw=True)
                        else:
                            g1_p1, g1_p2 = update_elo(p1_elo, p2_elo, game1 == 1.0)

                        # Game 2 calculation
                        if game2 == 0.5:
                            g2_p1, g2_p2 = update_elo(p1_elo, p2_elo, draw=True)
                        else:
                            g2_p1, g2_p2 = update_elo(p1_elo, p2_elo, game2 == 1.0)

                        # Average results
                        final_p1 = (g1_p1 + g2_p1) / 2
                        final_p2 = (g1_p2 + g2_p2) / 2

                        # Calculate stats
                        p1_wins = sum(1 for r in [game1, game2] if
                                      (r == 1.0 and p1_id == ctx.author.id) or (r == 0.0 and p2_id == ctx.author.id))
                        p1_losses = 2 - p1_wins - sum(1 for r in [game1, game2] if r == 0.5)
                        p1_draws = sum(1 for r in [game1, game2] if r == 0.5)

                        p2_wins = 2 - p1_wins - p1_draws
                        p2_losses = p1_wins
                        p2_draws = p1_draws

                        # Update database
                        update_player_stats(p1_id, final_p1, p1_wins, p1_losses, p1_draws)
                        update_player_stats(p2_id, final_p2, p2_wins, p2_losses, p2_draws)

                        await ctx.send(
                            f"✅ Both games confirmed! Updated:\n"
                            f"<@{p1_id}>: {p1_wins}W {p1_losses}L {p1_draws}D | ELO: {p1_elo:.0f}→{final_p1:.0f}\n"
                            f"<@{p2_id}>: {p2_wins}W {p2_losses}L {p2_draws}D | ELO: {p2_elo:.0f}→{final_p2:.0f}"
                        )

                    # Cleanup pending reports
                    c.execute("DELETE FROM pending_reps WHERE pairing_id=?", (pairing_id,))
                    conn.commit()
                else:
                    await ctx.send("❌ Already reported! Waiting for opponent's confirmation.")
            else:
                # Create new pending report
                c.execute('''INSERT INTO pending_reps
                                 (pairing_id, reporter_id, result, game_number)
                             VALUES (?, ?, ?, ?)''',
                          (pairing_id, ctx.author.id, result, game_number))
                conn.commit()
                await ctx.send(
                    f"⚠️ Reported game {game_number}! {opponent.mention} confirm with:\n"
                    f"`$rep {'l' if result == 'w' else 'w' if result == 'l' else 'd'} "
                    f"@{ctx.author.name} {game_number}`"
                )

        else:
            # ====== Regular Match Handling ======
            pending_rep = get_pending_rep(opponent.id, ctx.author.id)

            if pending_rep:
                pending_result = pending_rep[3]
                valid_confirmation = (
                        (result == 'w' and pending_result == 'l') or
                        (result == 'l' and pending_result == 'w') or
                        (result == 'd' and pending_result == 'd')
                )

                if valid_confirmation:
                    reporter_elo = reporter_data[1]
                    opponent_elo = opponent_data[1]

                    if result == 'w':
                        new_reporter_elo, new_opponent_elo = update_elo(reporter_elo, opponent_elo)
                        rep_wins, rep_losses, rep_draws = 1, 0, 0
                        opp_wins, opp_losses, opp_draws = 0, 1, 0
                    elif result == 'l':
                        new_opponent_elo, new_reporter_elo = update_elo(opponent_elo, reporter_elo)
                        rep_wins, rep_losses, rep_draws = 0, 1, 0
                        opp_wins, opp_losses, opp_draws = 1, 0, 0
                    else:
                        new_reporter_elo, new_opponent_elo = update_elo(reporter_elo, opponent_elo, draw=True)
                        rep_wins, rep_losses, rep_draws = 0, 0, 1
                        opp_wins, opp_losses, opp_draws = 0, 0, 1

                    update_player_stats(ctx.author.id, new_reporter_elo, rep_wins, rep_losses, rep_draws)
                    update_player_stats(opponent.id, new_opponent_elo, opp_wins, opp_losses, opp_draws)
                    delete_pending_rep(pending_rep[0])

                    await ctx.send(
                        f"✅ Match confirmed!\n"
                        f"{ctx.author.mention}: {rep_wins}W {rep_losses}L {rep_draws}D | ELO: {reporter_elo:.0f}→{new_reporter_elo:.0f}\n"
                        f"{opponent.mention}: {opp_wins}W {opp_losses}L {opp_draws}D | ELO: {opponent_elo:.0f}→{new_opponent_elo:.0f}"
                    )
                else:
                    await ctx.send("❌ Results don't match! Report the opposite result.")
            else:
                add_pending_rep(ctx.author.id, opponent.id, result)
                await ctx.send(
                    f"📩 Match reported! {opponent.mention} confirm with:\n"
                    f"`$rep {'l' if result == 'w' else 'w' if result == 'l' else 'd'} @{ctx.author.name}`"
                )

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
    finally:
        conn.close()

@bot.command(name='cancel')
async def cancel_pending_match(ctx, result: str, opponent: discord.Member):
    """Cancel your last pending match with the specified opponent"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    result = result.lower()
    if result not in ['w', 'l', 'd']:
        await ctx.send("Invalid result. Use 'w' for win, 'l' for loss, or 'd' for draw.")
        return

    if ctx.author.id == opponent.id:
        await ctx.send("You can't cancel a match with yourself!")
        return

    pending_rep = get_pending_rep(ctx.author.id, opponent.id)

    if not pending_rep:
        await ctx.send(f"No pending match found against {opponent.mention} to cancel!")
        return

    if pending_rep[3].lower() != result:
        await ctx.send(f"Result doesn't match your pending match against {opponent.mention}!")
        return

    delete_pending_rep(pending_rep[0])
    await ctx.send(f"✅ Successfully canceled your pending match against {opponent.mention}!")


@bot.command(name='stats')
async def show_stats(ctx, player: discord.Member = None):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    target = player or ctx.author
    data = get_player_data(target.id)

    if not data:
        if target == ctx.author:
            await ctx.send(f"You're not registered! Use `$register` to join the ELO system.")
        else:
            await ctx.send(f"{target.name} isn't registered with the ELO system.")
        return

    embed = discord.Embed(title=f"Stats for {target.name}", color=0x00ff00)
    embed.add_field(name="ELO", value=f"{data[1]:.0f}")
    embed.add_field(name="Wins", value=data[2])
    embed.add_field(name="Losses", value=data[3])
    embed.add_field(name="Draws", value=data[4])

    total_games = data[2] + data[3] + data[4]
    embed.add_field(name="Total Games", value=total_games)

    if data[2] + data[3] > 0:
        win_rate = (data[2] / total_games) * 100
        embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%")

    await ctx.send(embed=embed)

@bot.command(name='leaderboard')
async def show_leaderboard(ctx, *args):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    # Default values
    limit = 10
    role_name = None

    # Parse arguments
    for arg in args:
        if arg.isdigit():
            limit = min(max(1, int(arg)), 25)
        else:
            if role_name is None:
                role_name = arg
            else:
                role_name += " " + arg

    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()

    # Base query
    query = "SELECT id, elo, wins, losses, draws FROM players"
    params = ()

    # Role filtering
    role = None
    if role_name:
        role = discord.utils.find(lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles)
        if not role:
            await ctx.send(f"Role '{role_name}' not found!")
            conn.close()
            return

        member_ids = [str(m.id) for m in role.members]
        if not member_ids:
            await ctx.send(f"No players found with the '{role.name}' role!")
            conn.close()
            return

        query += " WHERE id IN (" + ",".join(["?"] * len(member_ids)) + ")"
        params = tuple(member_ids)

    # Get total player count
    c.execute(
        "SELECT COUNT(*) FROM players" + (" WHERE id IN (" + ",".join(["?"] * len(member_ids)) + ")" if role else ""),
        params if role else ())
    total_players = c.fetchone()[0]

    # Get user data and rank if registered
    user_data = get_player_data(ctx.author.id)
    user_rank = None
    user_surrounding = []
    show_user_stats = True  # Flag to determine if we should show user stats

    if user_data:
        # Get user's exact rank
        rank_query = "SELECT COUNT(*) FROM players WHERE elo > ?"
        if role:
            rank_query += " AND id IN (" + ",".join(["?"] * len(member_ids)) + ")"
            # Check if user has the role when filtering by role
            member = ctx.guild.get_member(ctx.author.id)
            show_user_stats = role in member.roles if member else False

        c.execute(rank_query, (user_data[1],) + (params if role else ()))
        user_rank = c.fetchone()[0] + 1

        # Get surrounding ranks if not in top limit and we're showing user stats
        if user_rank > limit and show_user_stats:
            # Get top players
            c.execute(query + " ORDER BY elo DESC LIMIT ?", params + (limit,))
            top_players = c.fetchall()

            # Get user's surrounding ranks (rank-1, rank, rank+1)
            offset = max(0, user_rank - 2)
            c.execute(query + " ORDER BY elo DESC LIMIT 3 OFFSET ?", params + (offset,))
            user_surrounding = c.fetchall()
        else:
            # If user is in top limit, just get top players
            c.execute(query + " ORDER BY elo DESC LIMIT ?", params + (limit,))
            top_players = c.fetchall()
    else:
        # If user not registered, just get top players
        c.execute(query + " ORDER BY elo DESC LIMIT ?", params + (limit,))
        top_players = c.fetchall()
        show_user_stats = False

    conn.close()

    if not top_players and not user_surrounding:
        msg = "No players found"
        if role:
            msg += f" with the '{role.name}' role"
        msg += "! Use `$register` to join."
        await ctx.send(msg)
        return

    # Create embed
    title = f"🏆 Top {limit} Leaderboard"
    if role:
        title += f" ({role.name})"
    title += " 🏆"

    embed = discord.Embed(title=title, color=role.color if role else 0xffd700)

    # Add top players
    displayed_ranks = set()
    for i, (player_id, elo, wins, losses, draws) in enumerate(top_players, 1):
        try:
            member = await ctx.guild.fetch_member(player_id)
            name = member.display_name
            if role and role in member.roles:
                name = f"{name} {str(role)}"
        except:
            name = f"Unknown Player ({player_id})"

        games = wins + losses + draws
        if games > 0:
            win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
            stats = f"**{elo:.0f} ELO** | {wins}W {losses}L {draws}D ({win_rate:.1f}%)"
        else:
            stats = f"**{elo:.0f} ELO** | No games played"

        embed.add_field(name=f"{i}. {name}", value=stats, inline=False)
        displayed_ranks.add(i)

    # Add surrounding ranks if user not in top limit and we should show them
    if user_surrounding and show_user_stats:
        embed.add_field(name="\n...", value="...", inline=False)

        for i, (player_id, elo, wins, losses, draws) in enumerate(user_surrounding, user_rank - 1):
            if i in displayed_ranks:
                continue

            try:
                member = await ctx.guild.fetch_member(player_id)
                name = member.display_name
                highlight = "**>>>** " if player_id == ctx.author.id else ""

                if role and role in member.roles:
                    name = f"{name} {str(role)}"
            except:
                name = f"Unknown Player ({player_id})"
                highlight = ""

            games = wins + losses + draws
            if games > 0:
                win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
                stats = f"**{elo:.0f} ELO** | {wins}W {losses}L {draws}D ({win_rate:.1f}%)"
            else:
                stats = f"**{elo:.0f} ELO** | No games played"

            embed.add_field(name=f"{highlight}{i}. {name}", value=stats, inline=False)

    # Add user stats if registered and we should show them
    if user_data and show_user_stats:
        user_games = user_data[2] + user_data[3] + user_data[4]
        if user_games > 0:
            win_rate = (user_data[2] / (user_data[2] + user_data[3])) * 100 if (user_data[2] + user_data[3]) > 0 else 0
            user_stats = f"**{user_data[1]:.0f} ELO** | {user_data[2]}W {user_data[3]}L {user_data[4]}D ({win_rate:.1f}%)"
        else:
            user_stats = f"**{user_data[1]:.0f} ELO** | No games played"

        if user_rank:
            embed.add_field(name=f"\nYour Rank: #{user_rank} of {total_players}", value=user_stats, inline=False)
    elif not role or (role and ctx.author.id in [m.id for m in role.members]):
        # Only show registration prompt if not filtering by role or if user has the role
        if not role or (role and role in ctx.author.roles):
            embed.add_field(name="\nYou're not registered!", value="Use `$register` to join the leaderboard",
                            inline=False)

    await ctx.send(embed=embed)


@bot.command(name='signup')
async def signup_player(ctx):
    """Sign up for the current season"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    player_id = ctx.author.id
    if not get_player_data(player_id):
        await ctx.send(f"You need to register first with `$register`!")
        return

    conn = sqlite3.connect('elo_bot.db')
    c = conn.cursor()

    # Check if season is active
    c.execute("SELECT active FROM seasons ORDER BY season_number DESC LIMIT 1")
    season_active = c.fetchone()[0]

    if not season_active:
        # Update signup status
        c.execute("UPDATE players SET signed_up=1 WHERE id=?", (player_id,))
        conn.commit()
        await ctx.send(f"✅ {ctx.author.mention} has signed up for the current season!")
    else:
        await ctx.send("❌ Season is already active")

    conn.close()


@bot.command(name='start_season')
@commands.has_permissions(manage_roles=True)
async def start_season(ctx):
    """Start a new season (Admin only)"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()

        # Get current season number
        c.execute("SELECT season_number FROM seasons ORDER BY season_number DESC LIMIT 1")
        current_season = c.fetchone()[0]

        # Check if there's already an active season
        c.execute("SELECT active FROM seasons WHERE season_number=?", (current_season,))
        if c.fetchone()[0]:
            await ctx.send("❌ There's already an active season!")
            conn.close()
            return

        # Update roles for signed up players
        await update_player_roles(ctx)

        # Generate pairings
        await generate_pairings(ctx, current_season)

        # Mark season as active
        c.execute("UPDATE seasons SET active=1 WHERE season_number=?", (current_season,))
        conn.commit()

        await ctx.send(f"✅ Season {current_season} has started! Players can no longer sign up")

    except Exception as e:
        await ctx.send(f"❌ Error starting season: {e}")
    finally:
        conn.close()


@bot.command(name='end_season')
@commands.has_permissions(manage_roles=True)
async def end_season(ctx):
    """End the current season (Admin only)"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()

        # Get current active season
        c.execute("SELECT season_number FROM seasons WHERE active=1")
        result = c.fetchone()

        if not result:
            await ctx.send("❌ No active season to end!")
            conn.close()
            return

        current_season = result[0]

        # Reset all signups
        c.execute("UPDATE players SET signed_up=0")

        # Create new season
        new_season = current_season + 1
        c.execute("INSERT INTO seasons (season_number, active) VALUES (?, 0)", (new_season,))

        # Mark old season as inactive
        c.execute("UPDATE seasons SET active=0 WHERE season_number=?", (current_season,))
        conn.commit()

        await ctx.send(f"✅ Season {current_season} has ended. Season {new_season} is ready to start!")

    except Exception as e:
        await ctx.send(f"❌ Error ending season: {e}")
    finally:
        conn.close()




@bot.command(name='help')
async def show_help(ctx):
    """Show all available commands and how to use them"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    embed = discord.Embed(
        title="🏆 ELO Bot Help 🏆",
        description="Here are all the available commands:",
        color=0x00ff00
    )

    # Basic commands
    embed.add_field(
        name="🔹 Registration",
        value=(
            "`$register` - Register yourself in the ELO system\n"
            "`$signup` - Sign up for the current season"
        ),
        inline=False
    )

    # Match reporting
    embed.add_field(
        name="🔹 Match Reporting",
        value=(
            "`$rep [w/l/d] @opponent` - Report a match result\n"
            "   • For season matches: Records result in pairings\n"
            "   • For normal matches: Requires opponent confirmation\n"
            "`$cancel [w/l/d] @opponent` - Cancel a pending match report"
        ),
        inline=False
    )

    # Stats commands
    embed.add_field(
        name="🔹 Statistics",
        value=(
            "`$stats` - Show your stats\n"
            "`$stats @player` - Show another player's stats\n"
            "`$leaderboard` - Show top 10 players\n"
            "`$leaderboard [number]` - Show top X players (max 25)\n"
            "`$leaderboard [role name]` - Show leaderboard for a specific role\n"
            "`$leaderboard [number] [role name]` - Combined options"
        ),
        inline=False
    )

    # Season commands
    embed.add_field(
        name="🔹 Season Management",
        value=(
            "`$pairings` - Show your current season pairings\n"
            "`$pairings [season]` - Show pairings for a specific season\n"
            "`$pairings [season] [group]` - Show pairings for season and group"
        ),
        inline=False
    )

    # Admin commands
    if ctx.author.guild_permissions.manage_roles:
        embed.add_field(
            name="🔹 Admin Commands",
            value=(
                "`$update_roles` - Update all signed-up players' roles based on ELO\n"
                "`$start_season` - Start a new season (generates pairings)\n"
                "`$end_season` - End the current season\n"
                "   • Requires a properly configured 'elo_roles.csv' file"
            ),
            inline=False
        )

    # Additional info
    embed.add_field(
        name="ℹ️ How It Works",
        value=(
            "**Regular Matches:**\n"
            "1. Both players must `$register` first\n"
            "2. One player reports the match with `$rep`\n"
            "3. The other player confirms by reporting the opposite result\n"
            "4. ELO is updated automatically after confirmation\n\n"
            "**Season Matches:**\n"
            "1. Admin starts season with `$start_season`\n"
            "2. Players sign up with `$signup`\n"
            "3. Pairings are generated automatically\n"
            "4. Report results with `$rep` (no confirmation needed)\n"
            "5. Admin ends season with `$end_season`"
        ),
        inline=False
    )

    embed.set_footer(text=f"Bot is restricted to #{bot.get_channel(ALLOWED_CHANNEL_ID).name}")

    await ctx.send(embed=embed)


from fuzzywuzzy import fuzz  # Requires: pip install fuzzywuzzy python-Levenshtein


class PairingsPaginator(discord.ui.View):
    def __init__(self, embeds, author):
        super().__init__(timeout=60)
        self.embeds = embeds
        self.current_page = 0
        self.author = author
        self.message = None
        self._update_buttons()

    def _update_buttons(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.embeds) - 1
        self.page_count.label = f"Page {self.current_page + 1}/{len(self.embeds)}"

    @discord.ui.button(label="◀", style=discord.ButtonStyle.blurple)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.grey, disabled=True)
    async def page_count(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            return
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.current_page], view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)

@bot.command(name='pairings')
async def show_pairings(ctx, *, args: str = None):
    """Show your current season group pairings or specify season/group"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    conn = None
    try:
        conn = sqlite3.connect('elo_bot.db')
        c = conn.cursor()

        # Get current active season
        c.execute("SELECT season_number FROM seasons WHERE active=1")
        season_result = c.fetchone()
        current_season = season_result[0] if season_result else None

        if not args:
            # Default to current season and player's group
            if not current_season:
                await ctx.send("❌ No active season!")
                return

            player_id = ctx.author.id
            c.execute('''SELECT group_name
                         FROM pairings
                         WHERE season_number = ?
                           AND (player1_id = ? OR player2_id = ?) LIMIT 1''', (current_season, player_id, player_id))
            group_result = c.fetchone()

            if not group_result:
                await ctx.send("❌ You are not in any group for the current season!")
                return

            group_name = group_result[0]
            season = current_season
            title = f"Your Group Pairings - Season {season}"

        else:
            # Parse arguments with quote handling
            try:
                parts = shlex.split(args)
            except:
                parts = args.split()

            season = None
            group_name = None

            # Check for season number
            if parts[0].isdigit():
                season = int(parts[0])
                group_name = ' '.join(parts[1:]) if len(parts) > 1 else None
            else:
                group_name = ' '.join(parts)
                season = current_season  # Default to current season if only group specified

            # Validate season exists
            if season:
                c.execute("SELECT 1 FROM seasons WHERE season_number=?", (season,))
                if not c.fetchone():
                    await ctx.send(f"❌ Season {season} doesn't exist!")
                    return

            # Validate group exists in season
            if group_name:
                c.execute('''SELECT DISTINCT group_name
                             FROM pairings
                             WHERE season_number = ?''', (season,))
                valid_groups = [row[0].lower() for row in c.fetchall()]

                if group_name.lower() not in valid_groups:
                    suggestions = [g for g in valid_groups if group_name.lower() in g]
                    msg = f"❌ Group '{group_name}' not found in season {season}!"
                    if suggestions:
                        msg += f"\nDid you mean: {', '.join(suggestions[:3])}?"
                    await ctx.send(msg)
                    return

            title = f"Pairings - Season {season}" + (f", {group_name}" if group_name else "")

        # Build query
        query = '''SELECT player1_id, player2_id, result1, result2
                   FROM pairings
                   WHERE season_number = ?'''
        params = [season]

        if group_name:
            query += " AND LOWER(group_name)=LOWER(?)"
            params.append(group_name.strip())

        query += " ORDER BY id"
        c.execute(query, params)
        pairings = c.fetchall()

        if not pairings:
            await ctx.send(
                f"❌ No pairings found for {'season ' + str(season) if season else ''}{' group ' + group_name if group_name else ''}!")
            return

        # Format pairings into pages
        embeds = []
        current_embed = None
        char_count = 0
        MAX_EMBED_CHARS = 4096

        for idx, pairing in enumerate(pairings, 1):
            p1, p2, r1, r2 = pairing

            # Get member names
            try:
                p1_name = (await ctx.guild.fetch_member(p1)).display_name[:20]
            except:
                p1_name = f"Player {p1}"
            try:
                p2_name = (await ctx.guild.fetch_member(p2)).display_name[:20]
            except:
                p2_name = f"Player {p2}"

            res1 = "Pending" if r1 is None else f"{r1:.1f}"
            res2 = "Pending" if r2 is None else f"{r2:.1f}"

            entry = (
                f"**Match {idx}**\n"
                f"⚔ {p1_name} vs {p2_name}\n"
                f"• Game 1: {res1.ljust(7)} • Game 2: {res2}\n\n"
            )
            entry_length = len(entry)

            if not current_embed or (char_count + entry_length) > MAX_EMBED_CHARS:
                if current_embed:
                    embeds.append(current_embed)
                current_embed = discord.Embed(color=0x00ff00)
                current_embed.description = ""
                char_count = 0
                page_num = len(embeds) + 1
                current_embed.title = f"{title} - Page {page_num}"

            current_embed.description += entry
            char_count += entry_length

        if current_embed:
            embeds.append(current_embed)

        # Send results
        if len(embeds) == 1:
            await ctx.send(embed=embeds[0])
        else:
            view = PairingsPaginator(embeds, ctx.author)
            view.message = await ctx.send(embed=embeds[0], view=view)

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
    finally:
        if conn:
            conn.close()


# Events
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'Commands restricted to channel ID: {ALLOWED_CHANNEL_ID}')
    print('------')
    bot.loop.create_task(clean_old_pending_matches())


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return
    print(f"Error in command {ctx.command}: {error}")


@bot.event
async def on_message(message):
    if message.content.startswith(bot.command_prefix):
        allowed, _ = check_channel(message)
        if allowed:
            await bot.process_commands(message)


# Run the bot
try:
    bot.run(BOT_TOKEN)
except discord.LoginError:
    print("Invalid bot token in config.csv. Please check your token.")
except Exception as e:
    print(f"Error starting bot: {e}")