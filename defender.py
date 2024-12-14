from __future__ import annotations

from discord.ext import tasks

# from discord.ext.commands import Context, Greedy
from discord.ext import commands
from discord import Member, Guild, Embed, app_commands
import discord

from taskmaster import suppress

from colorama import init

from models import Defender
from typing import Union, Optional
import io


bot = Defender("$")

# TODO: Slash commands?



async def safe_prune(member: Member):
    # All roles should be removed from the member

    # Member should not be banned if they are moderators
    if member.guild_permissions.ban_members or member.guild_permissions.kick_members:
        return None

    # Remove all roles so channels can't be accessed
    for r in member.guild.roles:
        if member.get_role(r.id):
            await member.remove_roles(r, reason="Violated The Rules")

    try:
        return await bot.prune_member(member)
    except (discord.HTTPException, discord.Forbidden):
        return None


async def safe_ban(
    user: Union[discord.User, Member],
    interaction: discord.Interaction,
    reason: Optional[str] = None,
):
    if isinstance(user, Member):
        if user.guild_permissions.ban_members or user.guild_permissions.kick_members:
            return None

    async with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
        await interaction.guild.ban(user, reason=reason)
        await interaction.followup.send(
            embed=Embed(title="Banned")
            .add_field(name="Member ID", value=user.id)
            .add_field(name="Reason", value=reason or "None Given")
            .add_field(name="Name", value=user.name)
        )


@tasks.loop(minutes=15)
async def prune_loop():
    for guild in bot.guilds:
        gm = await bot.get_guild_model(guild.id)
        if gm.moderator_channel and gm.prune_role_id:
            await bot.ban_pruned_members(gm.guild_id)


@bot.event
async def on_ready():
    await bot.init_db()
    # Try a prune loop immediatley if all requirements
    # were ment to perform one if we originally did a startup before...
    for guild in bot.guilds:
        gm = await bot.get_guild_model(guild.id)
        if gm.moderator_channel and gm.prune_role_id:
            await bot.ban_pruned_members(gm.guild_id)
    prune_loop.start()


@bot.event
async def on_guild_join(guild: Guild):
    await bot.create_guild_model(guild)


@bot.event
async def on_guild_remove(guild: Guild):
    await bot.remove_guild_model(guild)


@bot.event
async def on_member_join(member: Member):
    gm = await bot.get_guild_model(member.guild.id)
    if gm.moderator_channel and gm.prune_role_id:
        await bot.check_member(member)
        # Smart to run this when we can...
        await bot.ban_pruned_members(gm.guild_id)


@bot.tree.command(name="add-prune-role")
@commands.has_permissions(administrator=True)
async def register_prune_role(interaction: discord.Interaction, role: discord.Role):
    """Registers a prune Role to add to a user when joining a little too early, (reqiures admin)"""
    await interaction.response.defer()
    try:
        await bot.update_guild_prune_role(role.id, interaction.guild.id)
        await interaction.followup.send("Role Updated")
    except Exception as e:
        await interaction.followup.send(f"Error {e.__name__} {e}")


@bot.tree.command(name="add-mod-channel")
@commands.has_permissions(administrator=True)
async def register_moderator_channel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
    """Registers a prune Role to add to a user when joining a little too early, (reqiures admin)"""
    await interaction.response.defer()
    
    try:
        if not channel:
            channel = interaction.channel
        await bot.update_guild_mod_channel(channel.id, interaction.guild.id)
        await interaction.followup.send("Moderator Channel Registered")
    except Exception as e:
        await interaction.followup.send(f"Error {e.__name__} {e}")


@bot.tree.command(name="requirements")
@commands.bot_has_permissions(ban_members=True)
@commands.has_permissions(administrator=True)
async def check_requirements(interaction: discord.Interaction):
    """Checks Guild Requirements for pruning members""" 
    await interaction.response.defer()
    embed = Embed(title="Requirements")

    model = await bot.get_guild_model(interaction.guild.id)
    if not (model.moderator_channel or model.prune_role_id):
        embed.add_field(
            name="Set Prune Role",
            value="Incomplete" if not model.prune_role_id else "Complete",
        )
        embed.add_field(
            name="Set Moderation Channel",
            value="Incomplete" if not model.moderator_channel else "Complete",
        )
    else:
        embed.add_field(
            name="Good news",
            value="You did all the requirements for pruning away scammers/spammers/raiders etc...",
        )

    await interaction.followup.send(embed=embed)


# @bot.tree.command(name="prune")
# @commands.bot_has_permissions(ban_members=True, manage_roles=True)
# @commands.has_permissions(ban_members=True)
# async def prune_users(ctx:Context, members:Greedy[Member]):
#     """Prunes Single or Multiple members at a time useful for unsure ban-wipes"""

#     model = await bot.get_guild_model(ctx.guild.id)
#     if not (model.moderator_channel or model.prune_role_id):
#         embed = Embed(color=0x1f1137, title="Error Unfinished Requirements")
#         embed.add_field(name="Set Prune Role", value="Incomplete" if not model.prune_role_id else "Complete")
#         embed.add_field(name="Set Moderation Channel", value="Incomplete" if not model.moderator_channel else "Complete")
#         return await ctx.send(embed=embed)

#     await ctx.send("Pruning members...")

#     async for result in amap(safe_prune , members, concurrency=2):
#         if result is not None:
#             await ctx.send(f"Pruned \"{result.member_id}\"")


# TODO: Massban Command and Textfile/list Ban
# Where I upload a file of discordIDs and bans them all...


@bot.tree.command(
    name="massban",
    description="""Performs a masisve ban and bans Many Users given from a text file""",
)
@commands.bot_has_permissions(ban_members=True, manage_roles=True)
@commands.has_permissions(administrator=True)
@app_commands.describe(
    file="A Text file/list of members to ban by Developer ID Line for line, The User does not have to be in the server to perform a massban.",
    reason="Reason A Group of Users are being banned for.",
)
async def massban(
    interaction: discord.Interaction,
    file: discord.Attachment,
    reason: Optional[str] = None,
):
    """Performs a masisve ban and bans Many Users given from a text file"""
    await interaction.response.defer()

    if not file.filename.endswith(".txt"):
        return await interaction.channel.send(
            "ERROR: Invalid File Type, textfiles with IDs on newlines required"
        )
    data = await file.read()
    await interaction.followup.send("loading previous ban history to filter bans...")
    entries_filter = {entry.user.id async for entry in interaction.guild.bans()}
    await interaction.followup.send("Performing Massban")

    for i in data.splitlines(keepends=False):
        if uid := i.strip():
            if uid.isdigit():
                user_id = int(i.strip())
                if user_id in entries_filter:
                    continue

                async with suppress(discord.NotFound):
                    user = await bot.fetch_user(user_id)
                    await safe_ban(user=user, interaction=interaction, reason=reason)


@bot.tree.command(
    name="blacklist",
    description="""makes a blacklist of banned users in the discord server""",
)
@commands.bot_has_permissions(ban_members=True, manage_roles=True)
@commands.has_permissions(administrator=True)
async def get_blacklist(interaction: discord.Interaction):
    """Be very careful when using this command as other users can lookup bad people"""
    await interaction.response.defer()

    # TODO: Local Database for banned users?

    return await interaction.followup.send(
        file=discord.File(
            io.StringIO(
                "\n".join(
                    map(
                        str, {entry.user.id async for entry in interaction.guild.bans()}
                    )
                )
            ),
            filename="blacklist.txt",
        )
    )


# Inspired by EvilPauze
@bot.tree.command(
    name="lock-channel",
    description="""Locks down a discord server channel same to how EvilPauze works""",
)
@commands.bot_has_permissions(manage_channels=True)
@commands.has_permissions(manage_channels=True)
async def lock_channel(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    moderators: bool = True,
):
    await interaction.response.defer()
    channel = channel or interaction.channel

    assert channel, "No Channel Exists"

    ldm = await bot.create_lockdown(interaction.guild, channel)

    overwrite = discord.PermissionOverwrite()
    overwrite.send_messages = False
    overwrite.read_messages = True

    for role in interaction.guild.roles:
        if role.permissions.send_messages:
            if moderators and (
                role.permissions.ban_members
                or role.permissions.kick_members
                or role.permissions.deafen_members
            ):
                # Moderators can talk here to help with damage control
                continue

            await channel.set_permissions(role, overwrite)
            ldm.add_role(role)
    await bot.update_lockdown_role(ldm)
    await interaction.followup.send(f"""Channel {channel.name} is locked-down""")


@bot.tree.command(name="unlock-channel")
@commands.bot_has_permissions(manage_channels=True)
@commands.has_permissions(manage_channels=True)
async def unlock_channel(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None
):
    """Unlocks a discord channel that was perviously locked"""
    # TODO: defer wrapper?
    await interaction.response.defer()
    _channel = channel or interaction.channel
    ld = await bot.get_lockdown(interaction.guild, _channel)
    if not ld:
        return await interaction.followup.send(f"{_channel.name} was not locked down so your good to go." )
    else:
        await interaction.followup.send(f"Unlocking {_channel.name}...")
        for role in ld.roles:
            if dsc_role := interaction.guild.get_role(role.role_id):
                await _channel.set_permissions(dsc_role)
        await interaction.followup.send("refreshing database...")
        await bot.delete_lockdown(ld)
        await interaction.followup.send(embed=discord.Embed(title="Lockdown successfully freed").add_field(name="Channel", value=_channel.name))





def banner():
    pass


def banner():

    print(Fore.LIGHTBLUE_EX + """
    ____  _                          __   ____       ____               __         
   / __ \(_)_____________  _________/ /  / __ \___  / __/__  ____  ____/ /__  _____
  / / / / / ___/ ___/ __ \/ ___/ __  /  / / / / _ \/ /_/ _ \/ __ \/ __  / _ \/ ___/
 / /_/ / (__  ) /__/ /_/ / /  / /_/ /  / /_/ /  __/ __/  __/ / / / /_/ /  __/ /    
/_____/_/____/\___/\____/_/   \__,_/  /_____/\___/_/  \___/_/ /_/\__,_/\___/_/     
                                                                                   
                        Stopping Raids since 2024
                        
                        Version 0.0.2 By Calloc
""" + Fore.RESET)



if __name__ == "__main__":
    init(autoreset=True)
    banner()
    # This will be removed in a future update in replacement for the config.yaml file...
    with open("token.txt", "r") as token:
        bot.run(token.read().rstrip())

