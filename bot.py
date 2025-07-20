import discord
from discord.ext import commands
import sqlite3, math, csv, os, shlex
from constants import ROLES_CONFIG_FILE, SQLITEFILE
from cDatabase import *


def load_config():
    config = {}
    try:
        with open("config.csv", mode="r") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                config[row["setting"]] = row["value"]

        if "token" not in config or not config["token"]:
            raise ValueError("Bot token not found in config.csv")
        if "channel_id" not in config or not config["channel_id"]:
            raise ValueError("Channel ID not found in config.csv")

        return config
    except FileNotFoundError:
        raise FileNotFoundError("config.csv file not found")
    except Exception as e:
        raise Exception(f"Error reading config.csv: {str(e)}")


try:
    config = load_config()
    BOT_TOKEN = config["token"]
    ALLOWED_CHANNEL_ID = int(config["channel_id"])
    if "backup_channel_id" in config.keys():
        BACKUP_CHANNEL_ID = int(config["backup_channel_id"])
    else:
        BACKUP_CHANNEL_ID = None

except Exception as e:
    print(f"Configuration error: {e}")
    exit(1)


intents = discord.Intents.all()
bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)


init_db()


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


@bot.command(name="update_roles")
@commands.has_permissions(manage_roles=True)
async def update_player_roles(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:

        if not os.path.exists(ROLES_CONFIG_FILE):
            raise FileNotFoundError(f"'{ROLES_CONFIG_FILE}' not found in bot directory")

        role_ranges = []
        with open(ROLES_CONFIG_FILE, mode="r") as csvfile:
            reader = csv.DictReader(csvfile)
            if (
                not reader.fieldnames
                or "role" not in reader.fieldnames
                or "min elo" not in reader.fieldnames
                or "max elo" not in reader.fieldnames
            ):
                raise ValueError(
                    "CSV file must have headers: 'role', 'min elo', 'max elo'"
                )

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
                    raise ValueError(f"Invalid ELO values in row: {row}")

        if not role_ranges:
            raise ValueError("No valid role ranges found in the configuration file")

        role_ranges.sort(key=lambda x: x["min"], reverse=True)

        conn = sqlite3.connect(SQLITEFILE)
        c = conn.cursor()
        c.execute("SELECT id, elo FROM players WHERE signed_up=1")

        players = c.fetchall()
        c.execute("UPDATE players SET seasons_missed = 0 WHERE signed_up = 1")
        conn.close()

        if not players:
            await ctx.send("No signed up players found!")
            return

        updated_count = 0
        progress_msg = await ctx.send("Updating roles... 0%")

        for i, (player_id, elo) in enumerate(players):
            try:
                member = await ctx.guild.fetch_member(player_id)
                if not member:
                    continue

                new_role = None
                for role_range in role_ranges:
                    if role_range["min"] <= elo <= role_range["max"]:
                        new_role = discord.utils.get(
                            ctx.guild.roles, name=role_range["name"]
                        )
                        if not new_role:
                            await ctx.send(
                                f"⚠️ Role '{role_range['name']}' not found on server!"
                            )
                            continue
                        break

                if not new_role:
                    continue

                roles_to_remove = []
                for role_range in role_ranges:
                    existing_role = discord.utils.get(
                        member.roles, name=role_range["name"]
                    )
                    if existing_role:
                        roles_to_remove.append(existing_role)

                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)

                await member.add_roles(new_role)
                updated_count += 1

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

        await progress_msg.edit(content=f"Updating Roles ... 100%")

        conn = sqlite3.connect(SQLITEFILE)
        c = conn.cursor()
        c.execute("SELECT id, elo, seasons_missed FROM players WHERE signed_up=0")

        n_players = c.fetchall()
        c.execute(
            "UPDATE players SET seasons_missed = seasons_missed + 1 WHERE signed_up = 0"
        )
        conn.close()

        for i, (player_id, elo, missed_seasons) in enumerate(n_players):
            try:

                member = await ctx.guild.fetch_member(player_id)
                if not member:
                    continue

                if missed_seasons > 1:
                    if elo - 1380 > 10:
                        elo -= 10
                    else:
                        elo = 1380

                    c.execute(
                        "UPDATE players SET elo = ? WHERE id = ?", (elo, player_id)
                    )

                roles_to_remove = []
                for role_range in role_ranges:
                    existing_role = discord.utils.get(
                        member.roles, name=role_range["name"]
                    )
                    if existing_role:
                        roles_to_remove.append(existing_role)

                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove)

                updated_count += 1

                if (i + 1) % max(5, len(n_players) // 10) == 0:
                    progress = int((i + 1) / len(n_players) * 100)
                    await progress_msg.edit(content=f"Removing roles... {progress}%")

            except discord.Forbidden:
                await ctx.send("❌ Bot doesn't have permission to manage roles!")
                return
            except discord.HTTPException as e:
                print(f"HTTP Error updating {player_id}: {e}")
            except Exception as e:
                print(f"Error updating {player_id}: {e}")

        await progress_msg.delete()
        await ctx.send(
            f"✅ Successfully updated roles for {updated_count}/{len(players) + len(n_players)} registered players!"
        )

    except FileNotFoundError as e:
        await ctx.send(
            f"❌ {e}\nPlease create a '{ROLES_CONFIG_FILE}' file with columns: 'role', 'min elo', 'max elo'"
        )
    except ValueError as e:
        await ctx.send(f"❌ Invalid configuration: {e}")
    except Exception as e:
        await ctx.send(f"❌ Unexpected error: {e}")


def check_channel(ctx):
    if ctx.channel.id != ALLOWED_CHANNEL_ID:
        allowed_channel = bot.get_channel(ALLOWED_CHANNEL_ID)
        channel_name = (
            f"#{allowed_channel.name}"
            if allowed_channel
            else f"channel with ID {ALLOWED_CHANNEL_ID}"
        )
        return False, f"This command can only be used in {channel_name}!"
    return True, None


@bot.command(name="register")
async def register_player(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    player_id = ctx.author.id
    if get_player_data(player_id):
        await ctx.send(f"{ctx.author.mention}, you're already registered!")
        return

    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()
    c.execute("INSERT INTO players (id, elo) VALUES (?, ?)", (player_id, INITIAL_ELO))
    conn.commit()
    conn.close()

    await ctx.send(
        f"🎉 {ctx.author.mention} has been registered with an initial ELO of {INITIAL_ELO}!"
    )


@bot.command(name="rep")
async def report_match(ctx, result: str, opponent: discord.Member, game_number: int):
    allowed, error_msg = check_channel(ctx)
    if game_number not in [1, 2]:
        await ctx.send("❌ Invalid game number. pls provide 1 or 2 after")
        return
    if not allowed:
        await ctx.send(error_msg)
        return

    result = result.lower()
    if result not in ["w", "l", "d"]:
        await ctx.send("❌ Invalid result. Use 'w', 'l', or 'd'.")
        return

    if ctx.author.id == opponent.id:
        await ctx.send("❌ You can't report a match with yourself!")
        return

    reporter_data = get_player_data(ctx.author.id)
    opponent_data = get_player_data(opponent.id)
    if not reporter_data or not opponent_data:
        await ctx.send("❌ Both players must be registered!")
        return

    conn = sqlite3.connect(SQLITEFILE)
    try:
        c = conn.cursor()
        season_active = c.execute(
            "SELECT active FROM seasons ORDER BY season_number DESC LIMIT 1"
        ).fetchone()[0]

        if season_active:

            pairing = get_specific_pairing(ctx, opponent, c)
            print(pairing)
            if not pairing:
                await ctx.send("❌ No valid season pairing found!")
                return

            pairing_id, p1_id, p2_id, _, _ = pairing

            is_player1 = ctx.author.id == p1_id
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

            if existing_rep:

                if existing_rep[0] == opponent.id:
                    expected_result = {"w": "l", "l": "w", "d": "d"}[existing_rep[1]]
                    if result != expected_result:
                        await ctx.send(
                            "❌ Results don't match! Please report the opposite result."
                        )
                        return
                    else:
                        await ctx.send("✅ Game Successfully reported!.")

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
                    c.execute(
                        """SELECT result1, result2
                                 FROM pairings
                                 WHERE (player1_id = ? AND player2_id = ?)
                                   AND season_number = (SELECT season_number FROM seasons WHERE active = 1)
                        """,
                        (p1_id, p2_id),
                    )
                    game1, game2 = c.fetchone()

                    if game1 is not None and game2 is not None:
                        p1_elo = get_player_data(p1_id)[1]
                        p2_elo = get_player_data(p2_id)[1]

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
                        p1_losses = (
                            2 - p1_wins - sum(1 for r in [game1, game2] if r == 0.5)
                        )
                        p1_draws = sum(1 for r in [game1, game2] if r == 0.5)

                        p2_wins = 2 - p1_wins - p1_draws
                        p2_losses = p1_wins
                        p2_draws = p1_draws

                        update_player_stats(p1_id, g2_p1, p1_wins, p1_losses, p1_draws)
                        update_player_stats(p2_id, g2_p2, p2_wins, p2_losses, p2_draws)

                        await ctx.send(
                            f"✅ Both games confirmed! Updated:\n"
                            f"<@{p1_id}>: {p1_wins}W {p1_losses}L {p1_draws}D | ELO: {p1_elo:.0f}→{g2_p1:.0f}\n"
                            f"<@{p2_id}>: {p2_wins}W {p2_losses}L {p2_draws}D | ELO: {p2_elo:.0f}→{g2_p2:.0f}"
                        )

                    c.execute(
                        "DELETE FROM pending_reps WHERE pairing_id=?", (pairing_id,)
                    )
                    conn.commit()
                else:
                    await ctx.send(
                        "❌ Already reported! Waiting for opponent's confirmation."
                    )
            else:

                c.execute(
                    """INSERT INTO pending_reps
                                 (pairing_id, reporter_id, result, game_number)
                             VALUES (?, ?, ?, ?)""",
                    (pairing_id, ctx.author.id, result, game_number),
                )
                conn.commit()
                await ctx.send(
                    f"⚠️ Reported game {game_number}! {opponent.mention} confirm with:\n"
                    f"`$rep {'l' if result == 'w' else 'w' if result == 'l' else 'd'} "
                    f"@{ctx.author.name} {game_number}`"
                )

    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")
    finally:
        conn.close()


@bot.command(name="cancel")
async def cancel_pending_match(ctx, result: str, opponent: discord.Member):
    """Cancel your last pending match with the specified opponent"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    result = result.lower()
    if result not in ["w", "l", "d"]:
        await ctx.send(
            "Invalid result. Use 'w' for win, 'l' for loss, or 'd' for draw."
        )
        return

    if ctx.author.id == opponent.id:
        await ctx.send("You can't cancel a match with yourself!")
        return

    pairing = get_specific_pairing(ctx, opponent)
    pending_rep = get_pending_rep(ctx.author.id, pairing[0])

    if not pending_rep:
        await ctx.send(f"No pending match found against {opponent.mention} to cancel!")
        return

    if pending_rep[3].lower() != result:
        await ctx.send(
            f"Result doesn't match your pending match against {opponent.mention}!"
        )
        return

    delete_pending_rep(pending_rep[0])
    await ctx.send(
        f"✅ Successfully canceled your pending match against {opponent.mention}!"
    )


@bot.command(name="stats")
async def show_stats(ctx, player: discord.Member = None):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    target = player or ctx.author
    data = get_player_data(target.id)

    if not data:
        if target == ctx.author:
            await ctx.send(
                f"You're not registered! Use `$register` to join the ELO system."
            )
        else:
            await ctx.send(f"{target.name} isn't registered with the ELO system.")
        return

    embed = discord.Embed(title=f"Stats for {target.name}", color=0x00FF00)
    embed.add_field(name="ELO", value=f"{data[1]:.0f}")
    embed.add_field(name="Wins", value=data[2])
    embed.add_field(name="Losses", value=data[3])
    embed.add_field(name="Draws", value=data[4])

    total_games = data[2] + data[3] + data[4]
    embed.add_field(name="Total Games", value=total_games)

    if data[2] + data[3] > 0:
        win_rate = ((data[2] + 0.5 * data[4]) / total_games) * 100
        embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%")

    await ctx.send(embed=embed)


@bot.command("backup")
@commands.has_permissions(manage_roles=True)
async def backup_db(ctx):
    if BACKUP_CHANNEL_ID is None:
        await ctx.send("❌ Backup channel isn't setuped yet")
        return
    channel = bot.get_channel(BACKUP_CHANNEL_ID)
    await channel.send(
        f"BackUP: <t:{math.floor(datetime.now().timestamp())}>",
        file=discord.File(SQLITEFILE),
    )


@bot.command(name="leaderboard")
async def show_leaderboard(ctx, *args):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    limit = 10
    role_name = None

    for arg in args:
        if arg.isdigit():
            limit = min(max(1, int(arg)), 25)
        else:
            if role_name is None:
                role_name = arg
            else:
                role_name += " " + arg

    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()

    query = "SELECT id, elo, wins, losses, draws FROM players"
    params = ()

    role = None
    if role_name:
        role = discord.utils.find(
            lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles
        )
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

    c.execute(
        "SELECT COUNT(*) FROM players"
        + (" WHERE id IN (" + ",".join(["?"] * len(member_ids)) + ")" if role else ""),
        params if role else (),
    )
    total_players = c.fetchone()[0]

    user_data = get_player_data(ctx.author.id)
    user_rank = None
    user_surrounding = []
    show_user_stats = True

    if user_data:

        rank_query = "SELECT COUNT(*) FROM players WHERE elo > ?"
        if role:
            rank_query += " AND id IN (" + ",".join(["?"] * len(member_ids)) + ")"

            member = ctx.guild.get_member(ctx.author.id)
            show_user_stats = role in member.roles if member else False

        c.execute(rank_query, (user_data[1],) + (params if role else ()))
        user_rank = c.fetchone()[0] + 1

        if user_rank > limit and show_user_stats:

            c.execute(query + " ORDER BY elo DESC LIMIT ?", params + (limit,))
            top_players = c.fetchall()

            offset = max(0, user_rank - 2)
            c.execute(query + " ORDER BY elo DESC LIMIT 3 OFFSET ?", params + (offset,))
            user_surrounding = c.fetchall()
        else:

            c.execute(query + " ORDER BY elo DESC LIMIT ?", params + (limit,))
            top_players = c.fetchall()
    else:

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

    title = f"🏆 Top {limit} Leaderboard"
    if role:
        title += f" ({role.name})"
    title += " 🏆"

    embed = discord.Embed(title=title, color=role.color if role else 0xFFD700)

    displayed_ranks = set()
    for i, (player_id, elo, wins, losses, draws) in enumerate(top_players, 1):
        try:
            member = await ctx.guild.fetch_member(player_id)
            name = member.display_name[:20]
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

    if user_surrounding and show_user_stats:
        embed.add_field(name="\n...", value="...", inline=False)

        for i, (player_id, elo, wins, losses, draws) in enumerate(
            user_surrounding, user_rank - 1
        ):
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
                stats = (
                    f"**{elo:.0f} ELO** | {wins}W {losses}L {draws}D ({win_rate:.1f}%)"
                )
            else:
                stats = f"**{elo:.0f} ELO** | No games played"

            embed.add_field(name=f"{highlight}{i}. {name}", value=stats, inline=False)

    if user_data and show_user_stats:
        user_games = user_data[2] + user_data[3] + user_data[4]
        if user_games > 0:
            win_rate = (
                (user_data[2] / (user_data[2] + user_data[3])) * 100
                if (user_data[2] + user_data[3]) > 0
                else 0
            )
            user_stats = f"**{user_data[1]:.0f} ELO** | {user_data[2]}W {user_data[3]}L {user_data[4]}D ({win_rate:.1f}%)"
        else:
            user_stats = f"**{user_data[1]:.0f} ELO** | No games played"

        if user_rank:
            embed.add_field(
                name=f"\nYour Rank: #{user_rank} of {total_players}",
                value=user_stats,
                inline=False,
            )
    elif not role or (role and ctx.author.id in [m.id for m in role.members]):

        if not role or (role and role in ctx.author.roles):
            embed.add_field(
                name="\nYou're not registered!",
                value="Use `$register` to join the leaderboard",
                inline=False,
            )

    await ctx.send(embed=embed)


@bot.command(name="signup")
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

    conn = sqlite3.connect(SQLITEFILE)
    c = conn.cursor()

    c.execute("SELECT active FROM seasons ORDER BY season_number DESC LIMIT 1")
    season_active = c.fetchone()[0]

    if not season_active:

        c.execute("UPDATE players SET signed_up=1 WHERE id=?", (player_id,))
        conn.commit()
        await ctx.send(f"✅ {ctx.author.mention} has signed up for the current season!")
    else:
        await ctx.send("❌ Season is already active")

    conn.close()


@bot.command(name="start_season")
@commands.has_permissions(manage_roles=True)
async def start_season(ctx):
    """Start a new season (Admin only)"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:
        conn = sqlite3.connect(SQLITEFILE)
        c = conn.cursor()

        c.execute(
            "SELECT season_number FROM seasons ORDER BY season_number DESC LIMIT 1"
        )
        current_season = c.fetchone()[0]

        c.execute("SELECT active FROM seasons WHERE season_number=?", (current_season,))
        if c.fetchone()[0]:
            await ctx.send("❌ There's already an active season!")
            conn.close()
            return

        await update_player_roles(ctx)

        await generate_pairings(ctx, current_season)

        c.execute(
            "UPDATE seasons SET active=1 WHERE season_number=?", (current_season,)
        )
        conn.commit()

        await ctx.send(
            f"✅ Season {current_season} has started! Players can no longer sign up"
        )

    except Exception as e:
        await ctx.send(f"❌ Error starting season: {e}")
    finally:
        conn.close()


@bot.command(name="end_season")
@commands.has_permissions(manage_roles=True)
async def end_season(ctx):
    """End the current season (Admin only)"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:
        conn = sqlite3.connect(SQLITEFILE)
        c = conn.cursor()

        c.execute("SELECT season_number FROM seasons WHERE active=1")
        result = c.fetchone()

        if not result:
            await ctx.send("❌ No active season to end!")
            conn.close()
            return

        current_season = result[0]

        c.execute("UPDATE players SET signed_up=0")

        new_season = current_season + 1
        c.execute(
            "INSERT INTO seasons (season_number, active) VALUES (?, 0)", (new_season,)
        )

        c.execute(
            "UPDATE seasons SET active=0 WHERE season_number=?", (current_season,)
        )
        conn.commit()

        await ctx.send(
            f"✅ Season {current_season} has ended. Season {new_season} is ready to start!"
        )

    except Exception as e:
        await ctx.send(f"❌ Error ending season: {e}")
    finally:
        conn.close()


@bot.command(name="help")
async def show_help(ctx):
    """Show all available commands and how to use them"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    embed = discord.Embed(
        title="🏆 ELO Bot Help 🏆",
        description="Here are all the available commands:",
        color=0x00FF00,
    )

    embed.add_field(
        name="🔹 Registration",
        value=(
            "`$register` - Register yourself in the ELO system\n"
            "`$signup` - Sign up for the current season"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔹 Match Reporting",
        value=(
            "`$rep [w/l/d] @opponent game_number` - Report a match result\n"
            "   • For league matches: Records result in pairings\n"
            "`$cancel [w/l/d] @opponent` - Cancel a pending match report"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔹 Statistics",
        value=(
            "`$stats` - Show your stats\n"
            "`$stats @player` - Show another player's stats\n"
            "`$leaderboard` - Show top 10 players\n"
            "`$leaderboard [number]` - Show top X players (max 25)\n"
            "`$leaderboard [role name]` - Show leaderboard for a specific role\n"
            "`$leaderboard [number] [role name]` - Combined options"
            "`$groupranking [group name]` - shows the current rankings of the group you are requesting"
            "`$groupranking [group name] [season number]` - Shows the Rankings of the Specific Season"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔹 Season Management",
        value=(
            "`$pairings` - Show your current season pairings\n"
            "`$pairings [season]` - Show pairings for a specific season\n"
            "`$pairings [season] [group]` - Show pairings for season and group"
        ),
        inline=False,
    )

    if ctx.author.guild_permissions.manage_roles:
        embed.add_field(
            name="🔹 Admin Commands",
            value=(
                "`$update_roles` - Update all signed-up players' roles based on ELO\n"
                "`$start_season` - Start a new season (generates pairings)\n"
                "`$end_season` - End the current season\n"
                "   • Requires a properly configured 'elo_roles.csv' file"
            ),
            inline=False,
        )

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
        inline=False,
    )

    embed.set_footer(
        text=f"Bot is restricted to #{bot.get_channel(ALLOWED_CHANNEL_ID).name}"
    )

    await ctx.send(embed=embed)


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
    async def previous_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.author:
            return
        self.current_page -= 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    @discord.ui.button(label="Page 1/1", style=discord.ButtonStyle.grey, disabled=True)
    async def page_count(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        pass

    @discord.ui.button(label="▶", style=discord.ButtonStyle.blurple)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.author:
            return
        self.current_page += 1
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.current_page], view=self
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)


@bot.command(name="groupranking")
async def show_groupleaderboard(ctx, group, season="latest"):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    if season == "latest":
        season = get_latest_season()
    leaderboard = get_group_ranking(season, group)
    embed = discord.Embed(
        title="Rankings", description=f"Ranking of {group} in Season {season}"
    )
    for i, player in enumerate(leaderboard, 1):
        id = player["id"]
        try:
            member = await ctx.guild.fetch_member(id)
            name = member.display_name[:20]
        except discord.NotFound:
            name = "Player left Server"

        if ctx.author.id == id:
            embed.add_field(
                name=f"{i}.",
                value=f"**Name: {name}**, Score: {player["points"]}, SB: {player["sb"]}",
                inline=False,
            )
        else:
            embed.add_field(
                name=f"{i}.",
                value=f"Name: {name}, Score: {player["points"]}, SB: {player["sb"]}",
                inline=False,
            )
    await ctx.send(embed=embed)


@bot.command(name="pairings")
async def show_pairings(ctx, *, args: str = None):
    """Show your current season group pairings or specify season/group"""
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    conn = None
    try:
        conn = sqlite3.connect(SQLITEFILE)
        c = conn.cursor()

        c.execute("SELECT season_number FROM seasons WHERE active=1")
        season_result = c.fetchone()
        current_season = season_result[0] if season_result else None

        if not args:

            if not current_season:
                await ctx.send("❌ No active season!")
                return

            player_id = ctx.author.id
            c.execute(
                """SELECT group_name
                         FROM pairings
                         WHERE season_number = ?
                           AND (player1_id = ? OR player2_id = ?) LIMIT 1""",
                (current_season, player_id, player_id),
            )
            group_result = c.fetchone()

            if not group_result:
                await ctx.send("❌ You are not in any group for the current season!")
                return

            group_name = group_result[0]
            season = current_season
            title = f"Your Group Pairings - Season {season}"

        else:

            try:
                parts = shlex.split(args)
            except:
                parts = args.split()

            season = None
            group_name = None

            if parts[0].isdigit():
                season = int(parts[0])
                group_name = " ".join(parts[1:]) if len(parts) > 1 else None
            else:
                group_name = " ".join(parts)
                season = current_season

            if season:
                c.execute("SELECT 1 FROM seasons WHERE season_number=?", (season,))
                if not c.fetchone():
                    await ctx.send(f"❌ Season {season} doesn't exist!")
                    return

            if group_name:
                c.execute(
                    """SELECT DISTINCT group_name
                             FROM pairings
                             WHERE season_number = ?""",
                    (season,),
                )
                valid_groups = [row[0].lower() for row in c.fetchall()]

                if group_name.lower() not in valid_groups:
                    suggestions = [g for g in valid_groups if group_name.lower() in g]
                    msg = f"❌ Group '{group_name}' not found in season {season}!"
                    if suggestions:
                        msg += f"\nDid you mean: {', '.join(suggestions[:3])}?"
                    await ctx.send(msg)
                    return

            title = f"Pairings - Season {season}" + (
                f", {group_name}" if group_name else ""
            )

        query = """SELECT player1_id, player2_id, result1, result2
                   FROM pairings
                   WHERE season_number = ?"""
        params = [season]

        if group_name:
            query += " AND LOWER(group_name)=LOWER(?)"
            params.append(group_name.strip())

        query += " ORDER BY id"
        c.execute(query, params)
        pairings = c.fetchall()

        if not pairings:
            await ctx.send(
                f"❌ No pairings found for {'season ' + str(season) if season else ''}{' group ' + group_name if group_name else ''}!"
            )
            return

        embeds = []
        current_embed = None
        char_count = 0
        MAX_EMBED_CHARS = 4096

        for idx, pairing in enumerate(pairings, 1):
            p1, p2, r1, r2 = pairing

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
                current_embed = discord.Embed(color=0x00FF00)
                current_embed.description = ""
                char_count = 0
                page_num = len(embeds) + 1
                current_embed.title = f"{title} - Page {page_num}"

            current_embed.description += entry
            char_count += entry_length

        if current_embed:
            embeds.append(current_embed)

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


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    print(f"Commands restricted to channel ID: {ALLOWED_CHANNEL_ID}")
    print("------")
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


try:
    bot.run(BOT_TOKEN)
except discord.LoginError:
    print("Invalid bot token in config.csv. Please check your token.")
except Exception as e:
    print(f"Error starting bot: {e}")
