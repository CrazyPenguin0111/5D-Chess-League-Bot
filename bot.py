import asyncio
from datetime import datetime
import sqlite3
import discord
from discord.ext import commands
import math, csv, os, shlex
from constants import INITIAL_ELO, ROLES_CONFIG_FILE, SQLITEFILE
from database import (
    activate_season,
    bundle_leaderboard,
    clean_old_pending_matches,
    find_pairings_in_db,
    find_player_group,
    setup_future_season,
    delete_pending_rep,
    find_unsigned_players,
    generate_pairings,
    get_group_ranking,
    get_latest_season,
    get_pending_rep,
    get_player_data,
    find_signed_players,
    get_specific_pairing,
    punish_player,
    register_new_player,
    sign_up_player,
    update_match_history,
    update_missed_seasons,
    update_player_stats,
)
from database_initialiser import init_db
from logic import calculate_match_stats, get_role_ranges


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

        role_ranges = get_role_ranges()

        players = find_signed_players()

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

        n_players = find_unsigned_players()
        update_missed_seasons()
        for i, (player_id, elo, missed_seasons) in enumerate(n_players):
            try:

                member = await ctx.guild.fetch_member(player_id)
                if not member:
                    continue

                punish_player(player_id, elo, missed_seasons)

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

    register_new_player(player_id)

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

            pairing = get_specific_pairing(ctx, opponent)
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

            c.execute(
                """SELECT result1, result2
                   FROM pairings
                   WHERE (player1_id = ? AND player2_id = ?)
                     AND season_number = (SELECT season_number FROM seasons WHERE active = 1)
                """,
                (p1_id, p2_id),
            )

            game1, game2 = c.fetchone()
            if game_number == 1:
                if game1 is not None:
                    await ctx.send(
                        "❌ Results have already been reported cannot report result again."
                    )
                    return

            if game_number == 2:
                if game2 is not None:
                    await ctx.send(
                        "❌ Results have already been reported cannot report result again."
                    )
                    return

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

                        player1_new_stats, player2_new_stats = calculate_match_stats(
                            game1, game2, p1_elo, p2_elo
                        )

                        update_player_stats(
                            p1_id,
                            player1_new_stats["elo"],
                            player1_new_stats["wins"],
                            player1_new_stats["losses"],
                            player1_new_stats["draws"],
                        )
                        update_player_stats(
                            p2_id,
                            player2_new_stats["elo"],
                            player2_new_stats["wins"],
                            player2_new_stats["losses"],
                            player2_new_stats["draws"],
                        )

                        await ctx.send(
                            f"✅ Both games confirmed! Updated:\n"
                            f"<@{p1_id}>: {player1_new_stats["wins"]}W {player1_new_stats["losses"]}L {player1_new_stats["draws"]}D | ELO: {p1_elo:.0f}→{player1_new_stats["elo"]:.0f}\n"
                            f"<@{p2_id}>: {player2_new_stats["wins"]}W {player2_new_stats["losses"]}L {player2_new_stats["draws"]}D | ELO: {p2_elo:.0f}→{player2_new_stats["elo"]:.0f}"
                        )

                    c.execute(
                        "DELETE FROM pending_reps WHERE pairing_id=? AND game_number=?",
                        (pairing_id, game_number),
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
        return await ctx.send(error_msg)

    # 1️⃣ parse args
    limit = 10
    role_name = None
    for arg in args:
        if arg.isdigit():
            limit = min(max(1, int(arg)), 25)
        else:
            role_name = (role_name + " " + arg).strip() if role_name else arg

    role = None
    member_ids = None
    if role_name:
        role = discord.utils.find(
            lambda r: r.name.lower() == role_name.lower(), ctx.guild.roles
        )
        if not role:
            return await ctx.send(f"❌ Role '{role_name}' not found!")
        member_ids = [m.id for m in role.members]
        if not member_ids:
            return await ctx.send(f"❌ No players have the '{role.name}' role!")

    total_players, you, user_rank, surrounding, rows = await bundle_leaderboard(
        ctx.author.id, limit, member_ids
    )

    if not rows and not surrounding:
        msg = "❌ No players found"
        if role:
            msg += f" with the '{role.name}' role"
        return await ctx.send(msg + "! Use `$register` to join.")

    # 4️⃣ bulk‑resolve all needed Discord names
    all_ids = {r["id"] for r in rows} | {r["id"] for r in surrounding}
    names = {}
    # try cache first
    for uid in all_ids:
        m = ctx.guild.get_member(uid)
        if m:
            names[uid] = m.display_name[:20]
    missing = [uid for uid in all_ids if uid not in names]
    if missing:
        fetched = await asyncio.gather(
            *(ctx.guild.fetch_member(uid) for uid in missing), return_exceptions=True
        )
        # ensure we map results to the original IDs by index
        for idx, res in enumerate(fetched):
            uid = missing[idx]
            if isinstance(res, discord.Member):
                names[uid] = res.display_name[:20]
            else:
                names[uid] = f"Player {uid}"

    # 5️⃣ build embed
    title = f"🏆 Top {limit} Leaderboard"
    if role:
        title += f" ({role.name})"
    title += f" 🏆"

    embed = discord.Embed(title=title, color=role.color if role else 0xFFD700)
    displayed = set()

    # Top section
    for idx, r in enumerate(rows, start=1):
        pid, elo, w, l, d = r["id"], r["elo"], r["wins"], r["losses"], r["draws"]
        name = names.get(pid, f"Player {pid}")

        games = w + l + d
        rate = f"{(w/games)*100:.1f}%" if (w + l) > 0 else "—"
        stats = (
            f"**{elo:.0f} ELO** | {w}W {l}L {d}D ({rate})"
            if games
            else f"**{elo:.0f} ELO** | No games"
        )

        embed.add_field(name=f"{idx}. {name}", value=stats, inline=False)
        displayed.add(idx)

    # Your surrounding (if any)
    if surrounding and you:
        embed.add_field(name="", value="—", inline=False)
        for offset, r in enumerate(surrounding, start=user_rank - 1):
            idx = offset
            if idx in displayed:
                continue
            pid, elo, w, l, d = r["id"], r["elo"], r["wins"], r["losses"], r["draws"]
            name = names.get(pid, f"Player {pid}")
            prefix = "**>>>** " if pid == ctx.author.id else ""
            if role and role in ctx.guild.get_member(pid).roles:
                name += f" {role.mention}"

            games = w + l + d
            rate = f"{(w/(w+l))*100:.1f}%" if (w + l) > 0 else "—"
            stats = (
                f"**{elo:.0f} ELO** | {w}W {l}L {d}D ({rate})"
                if games
                else f"**{elo:.0f} ELO** | No games"
            )

            embed.add_field(name=f"{prefix}{idx}. {name}", value=stats, inline=False)

    # Your footer
    if you:
        embed.set_footer(text=f"Your rank: {user_rank} / {total_players}")
    else:
        embed.set_footer(text="Use $register to join the leaderboard")

    await ctx.send(embed=embed)


@bot.command(name="signup")
async def signup_player(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    player_id = ctx.author.id
    if not get_player_data(player_id):
        await ctx.send(f"You need to register first with `$register`!")
        return
    (_, season_active) = get_latest_season()

    if not season_active:
        sign_up_player(player_id)
        await ctx.send(f"✅ {ctx.author.mention} has signed up for the current season!")
    else:
        await ctx.send("❌ Season is already active")


@bot.command(name="start_season")
@commands.has_permissions(manage_roles=True)
async def start_season(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:

        (current_season, active) = get_latest_season()

        if active:
            await ctx.send("❌ There's already an active season!")
            return

        await update_player_roles(ctx)

        await generate_pairings(ctx, current_season)

        activate_season(current_season)

        await ctx.send(
            f"✅ Season {current_season} has started! Players can no longer sign up"
        )

    except Exception as e:
        await ctx.send(f"❌ Error starting season: {e}")


@bot.command(name="end_season")
@commands.has_permissions(manage_roles=True)
async def end_season(ctx):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return

    try:

        (old_season, _) = get_latest_season()

        if not old_season:
            await ctx.send("❌ No active season to end!")
            return

        new_season = old_season + 1
        setup_future_season(old_season, new_season)
        await ctx.send(
            f"✅ Season {old_season} has ended. Season {new_season} is ready to start!"
        )

    except Exception as e:
        await ctx.send(f"❌ Error ending season: {e}")


@bot.command(name="help")
async def show_help(ctx):
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
            "`$leaderboard [number] [role name]` - Combined options\n"
            "`$rankings [group name]` - shows the current rankings of the group you are requesting\n"
            "`$rankings [group name] [season number]` - Shows the Rankings of the Specific Season"
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
            "**Match Info:**\n"
            "1. Both players must `$register` first\n"
            "2. One player reports the match with `$rep`\n"
            "3. The other player confirms by reporting the opposite result\n"
            "4. ELO is updated automatically after confirmation\n\n"
            "**Season Info:**\n"
            "1. Admin starts season with `$start_season`\n"
            "2. Players sign up with `$signup`\n"
            "3. Players can see pairings with `$pairings`\n"
            "4. Report results with `$rep`\n"
            "5. Players can see group rankings with `$rankings`\n"
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


@bot.command(name="rankings")
async def show_groupleaderboard(ctx, group="own", season="latest"):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        await ctx.send(error_msg)
        return
    if season == "latest":
        (season, _) = get_latest_season()
    if group == "own":
        group = find_player_group(ctx.author.id, season)
    if "procrastination" in group.lower() or "lazy" in group.lower():
        group = "Pro League"
    if not group:
        await ctx.send(f"❌ Couldnt find your given Group in {season}")
        return
    leaderboard = get_group_ranking(season, group)

    if "advanced" in group.lower():
        color = discord.Color.yellow()
    elif "pro" in group.lower():
        color = discord.Color.red()
    elif "entry" in group.lower():
        color = discord.Color.blue()

    embed = discord.Embed(
        title="Rankings",
        description=f"Ranking of {group} in Season {season}",
        color=color,
    )
    embed_str = ""
    for i, player in enumerate(leaderboard, 1):
        id = player["id"]
        try:
            member = await ctx.guild.fetch_member(id)
            name = member.display_name[:20]
        except discord.NotFound:
            name = "Player left Server"

        if ctx.author.id == id:
            embed_str += f"**{i}. {name}, Score: {player['points']}, {player['sb']}**"

        else:
            embed_str += f"{i}. {name}, Score: {player['points']}, {player['sb']}\n"

    embed.add_field(name="", value=embed_str)
    await ctx.send(embed=embed)


@bot.command(name="pairings")
async def show_pairings(ctx, *, args: str = None):
    allowed, error_msg = check_channel(ctx)
    if not allowed:
        return await ctx.send(error_msg)

    if args:
        try:
            parts = shlex.split(args)
        except ValueError:
            parts = args.split()
        if parts[0].isdigit():
            season = int(parts[0])
            group_name = " ".join(parts[1:]) if len(parts) > 1 else None

        else:
            season = None
            group_name = " ".join(parts)
    else:
        season = None
        group_name = None

    try:
        pairings, season = find_pairings_in_db(ctx.player.id, season, group_name)
    except Exception as e:
        (errorcode,) = e.args
        match errorcode:
            case 1:
                await ctx.send("❌ No active season!")
                return
            case 2:
                await ctx.send(f"❌ Season {season} doesn't exist!")
                return
            case 3:
                await ctx.send("❌ You are not in any group for the current season!")
                return
            case _:
                await ctx.send(errorcode)
                return
    if not pairings:
        suffix = f", Group {group_name}" if group_name else ""
        await ctx.send(f"❌ No pairings found for Season {season}{suffix}!")
        return

    ids = {row["player1_id"] for row in pairings} | {
        row["player2_id"] for row in pairings
    }
    names = {}
    for uid in ids:
        m = ctx.guild.get_member(uid)
        if m:
            names[uid] = m.display_name[:20]
    missing = [uid for uid in ids if uid not in names]
    if missing:
        fetched = await asyncio.gather(
            *(ctx.guild.fetch_member(uid) for uid in missing), return_exceptions=True
        )
        # map each result back to its uid reliably
        for idx, res in enumerate(fetched):
            uid = missing[idx]
            if isinstance(res, discord.Member):
                names[uid] = res.display_name[:20]
            else:
                # fallback on error: still provide a display name for this uid
                names[uid] = f"Player {uid}"

    MAX_CHARS = 3800
    pages, desc = [], ""
    for i, r in enumerate(pairings, start=1):
        p1, p2 = r["player1_id"], r["player2_id"]
        res1 = f"{r['result1']:.1f}" if r["result1"] is not None else "Pending"
        res2 = f"{r['result2']:.1f}" if r["result2"] is not None else "Pending"

        if res1 == "1.0":
            win1 = names[p1]
        elif res1 == "0.0":
            win1 = names[p2]
        elif res1 == "0.5":
            win1 = "draw"
        else:
            win1 = res1

        if res2 == "1.0":
            win2 = names[p1]
        elif res2 == "0.0":
            win2 = names[p2]
        elif res2 == "0.5":
            win2 = "draw"
        else:
            win2 = res2

        entry = (
            f"**Match {i}**\n"
            f"⚔ {names[p1]} vs {names[p2]}\n"
            f"• Game 1: {win1.ljust(len(win1))}     • Game 2: {win2}\n\n"
        )
        if len(desc) + len(entry) > MAX_CHARS:
            pages.append(desc)
            desc = entry
        else:
            desc += entry
    if desc:
        pages.append(desc)
    title = f"Pairings - Season {season}"
    embeds = [
        discord.Embed(title=f"{title} — Page {idx+1}", description=page, color=0x00FF00)
        for idx, page in enumerate(pages)
    ]

    if len(embeds) == 1:
        await ctx.send(embed=embeds[0])
    else:
        view = PairingsPaginator(embeds, ctx.author)
        view.message = await ctx.send(embed=embeds[0], view=view)


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
