from datetime import datetime, timedelta
from typing import NamedTuple, Optional

import discord
from discord import Guild, Intents, Member, app_commands, Client
# from discord.ext.commands import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Field, Relationship, SQLModel, select, update, delete 
from sqlmodel.ext.asyncio.session import AsyncSession

import aiofiles
import yaml
from taskmaster import suppress, amap
from sqlalchemy.orm import selectinload


global PRUNE_DATE, CREATION_DATE_LIMIT

# ONE Prune A day keeps the assholes away...

# Prune Date After Joining guild, you can edit that as required
PRUNE_DATE = timedelta(days=1)

# Creation date After Creating discord account (About 6 months)
CREATION_DATE_LIMIT = timedelta(weeks=26)






class IDModel(SQLModel):
    """Subclass for applying primary keys accross multiple tables"""

    id: Optional[int] = Field(default=None, primary_key=True)


# === PRUNING ===

class GuildModel(IDModel, table=True):
    guild_id: int = Field(unique=True)
    prune_role_id: Optional[int] = None
    """Role ID for pruning users into a seperate private \
        channel that only moderators and admins can see..."""
    moderator_channel: Optional[int] = None
    """Where the bot needs to report pruned users to"""
    pruned_members: list["PrunedMember"] = Relationship(back_populates="guild")


class PrunedMember(IDModel, table=True):
    """A Member that is scheduled for pruning"""

    member_id: int
    """Discord Snowflake, the user could be apart of multiple \
    guilds ready to be pruned hence not being a unqiue key"""

    prune_date: datetime
    """The time to ban or remove a member from the server for\
        likely being an alt account or spammer"""

    reason: Optional[str] = None

    guild_id: Optional[int] = Field(default=None, foreign_key="guildmodel.id")
    """The id for the guild in the SQL-Database"""

    guild: Optional[GuildModel] = Relationship(back_populates="pruned_members")
    """The Guild assigned for the pruned member to get rid of\
        on ban-wipe"""



# ======================== LOCKDOWNS ======================== 

# Inspired by EvilPauze (https://github.com/Alex1304/evilpauze) meant to lockdown and also 
# fully recover factory settings of a channel when channel lockdown is considered nessesary
# This is essentially an attempt at upgrading these protocols...


# Since I am a guy who is no stranger to aggressive users I considered implementing EvilPauze 
# into my bot to make it more aggressive.

# I guess you can call this EvilCalloc if you'd like...


class LockdownChannel(IDModel, table=True):
    """A Channel that is considered to be on-lockdown"""
    channel_id:int
    guild_id:int
    """Text id channel being locked down"""
    date : datetime = datetime.now() 
    """Timestamp of a lockdown good for record-keeping..."""
    reason:Optional[str] = None
    """Reason for locking down the guild's text channel"""
    roles: list["LockdownRole"] = Relationship(back_populates="channel")

    def add_role(self, role:discord.Role):
        self.roles.append(LockdownRole(role_id=role.id))


class LockdownRole(IDModel, table=True):
    
    role_id:int
    """Discord Role ID being locked and unable to write text... (These can be filtered by role via command...)"""
    channel_id : Optional[int] = Field(default=None, foreign_key="lockdownchannel.id")
    channel:Optional[LockdownChannel] = Relationship(back_populates="roles")




class MissingPruneRole(Exception):
    """Owner/Admin didn't set Pruning Roles"""
    pass




class MissingPruneRole(Exception):
    """Owner/Admin didn't set Pruning Roles"""
    pass


class PrunedUser(NamedTuple):
    member: Member
    pruned_info: PrunedMember


class Defender(Client):
    def __init__(
        self, prefix = "?", intents=Intents.all(), dbname: str = "sqlite+aiosqlite:///defender.db"
    ) -> None:
        super().__init__(intents=intents)
        self.engine = create_async_engine(dbname)
        self.session = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tree = app_commands.CommandTree(self)
        self.command = self.tree.command

    

    async def sync_guild(self, id:int):
        """Syncs a guild-id to the enabled servers the bot is allowed to be in"""
        # You should configure these manually as a safety-mechanism...
        guildObject = discord.Object(id=id)
        self.tree.copy_global_to(guild=guildObject)
        return await self.tree.sync(guild=guildObject)



    async def setup_hook(self):
        
        async with aiofiles.open("config.yaml", "r") as cfg:
            data:dict[str] = yaml.safe_load(await cfg.read())

        for i in data["discordServerIds"]:
            await self.sync_guild(i)
            
        # SETUP GLOBALS UNLESS DEFAULTS ARE GIVEN...
        # TODO:
        # if data.get("prune-time"):
        #     PRUNE_DATE = timedelta(**data["prune-time"])
        # if data.get("creation-date-limit"):
        #     CREATION_DATE_LIMIT = timedelta(**data["creation-date-limit"])


    
    async def init_db(self):
        async with self.engine.begin() as e:
            await e.run_sync(IDModel.metadata.create_all)

    async def get_guild_model(self, guild_id: int) -> GuildModel:
        async with self.session() as session:
            scalar = await session.exec(
                select(GuildModel).where(GuildModel.guild_id == guild_id)
            )
            guild = scalar.one_or_none()
            if not guild:
                guild = await session.merge(GuildModel(guild_id=guild_id))
                await session.commit()

        return guild

    async def update_guild_prune_role(self, prune_role_id: int, guild_id: int):
        """Update current guild's prune role"""
        async with self.session() as session:
            await session.exec(
                update(GuildModel)
                .where(GuildModel.guild_id == guild_id)
                .values(prune_role_id=prune_role_id)
            )
            await session.commit()
        return await self.get_guild_model(guild_id)

    async def update_guild_mod_channel(self, moderator_channel_id: int, guild_id: int):
        """Update current guild's prune role"""
        async with self.session() as session:
            await session.exec(
                update(GuildModel)
                .where(GuildModel.guild_id == guild_id)
                .values(moderator_channel=moderator_channel_id)
            )
            await session.commit()
        return await self.get_guild_model(guild_id)

    async def get_pruned_member(self, snowflake: int, guild_id: int):
        async with self.session() as session:
            scalar = await session.exec(
                select(PrunedMember)
                .where(PrunedMember.member_id == snowflake)
                .where(PrunedMember.guild_id == guild_id)
            )
            pinfo = scalar.one_or_none()
            if not pinfo:
                return

            guild = self.get_guild(pinfo.guild_id)
            member = guild.get_member(pinfo.member_id)
        return PrunedUser(member, pinfo)

    async def prune_member(self, member: Member, reason: str = "Suspicious account"):
        """Applies pruned role to an existing member and will be awaiting execution/ban"""
        guildmodel = await self.get_guild_model(member.guild.id)
        await member.add_roles(guildmodel.prune_role_id, reason=reason)

        async with self.session() as s:
            pm = await s.merge(
                PrunedMember(
                    member_id=member.id,
                    prune_date=datetime.now() + PRUNE_DATE,
                    guild_id=member.guild.id,
                    reason=reason,
                )
            )
            await s.commit()

        return pm

    async def ban_pruned_members(self, guild_id: int):
        """Bans all members in a guild when the given deadline is met"""
        guild = self.get_guild(guild_id)
        gm = await self.get_guild_model(guild_id)
        channel = guild.get_channel(gm.moderator_channel)
        
        assert channel, "This command requires A Moderation channel"

        async with self.session() as s:
            scalar = await s.exec(
                select(PrunedMember)
                .where(PrunedMember.guild_id == guild_id)
                .where(PrunedMember.prune_date < datetime.now())
            )
            for user in scalar:
                async with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    # byebye asshole
                    await guild.ban(user.member_id, reason="Pruned For Suspicous Join/Behavior")
                    await s.delete(user)
                    await s.commit()


    async def ban_all_pruned_members(self, guild_id:int):
        """Bans all members in a guild whithout the deadline"""
        guild = self.get_guild(guild_id)
        gm = await self.get_guild_model(guild_id)
        channel = guild.get_channel(gm.moderator_channel)

        assert channel, "This command requires A Moderation channel"

        async with self.session() as s:
            scalar = await s.exec(
                select(PrunedMember)
                .where(PrunedMember.guild_id == guild_id)
            )
            for user in scalar:
                async with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    if member := guild.get_member(user.member_id):
                        # Byebye asshole...
                        try:
                            await member.ban(reason="Pruned For Suspicous Join/Behavior")
                        except:
                            await s.delete(user)
                            await s.commit()
                    else:
                        await guild.ban(user.member_id, reason="Pruned For Suspicous Join/Behavior")
                        await s.delete(user)
                        await s.commit()


    async def check_member(self, member:Member):
        if member.created_at > (datetime.now() - CREATION_DATE_LIMIT):  
            await self.prune_member(member)
            guild = self.get_guild(member.guild.id)
            gm = await self.get_guild_model(guild)
            await guild.get_channel(gm.moderator_channel).send(f"Pruned Member named:{member.name}    DeveloperID: {member.id}")
    
    async def remove_guild_model(self, guild:Guild):
        """Removes Guild and Pruned memebers scheduled for ban"""
        async with self.session() as s:
            await s.exec(delete(GuildModel).where(GuildModel.id == guild.id))
            await s.commit()
    
    async def create_guild_model(self, guild:Guild):
        """Creates a New guild Model"""
        async with self.session() as s:
            await s.merge(GuildModel(guild_id=guild.id))
            await s.commit()
    

    async def create_lockdown(self, guild:Guild, channel:discord.TextChannel):
        """Simillar to `EvilPauze` This will essentially create a lock for locking down and unlocking roles/settings from..."""
        async with self.session() as s:
            ldc = await s.merge(LockdownChannel(guild_id=guild.id, channel_id=channel.id))
            await s.commit()
        return ldc
    
    async def get_lockdown(self, guild:Guild, channel:discord.TextChannel):
        async with self.session() as s:
            scalar = await s.exec(
                select(LockdownChannel)
                .where(LockdownChannel.guild_id == guild.id)
                .where(LockdownChannel.channel_id == channel.id)
            )
            ld_channel = scalar.one_or_none()
        return ld_channel


    async def update_lockdown_role(self, ldc:LockdownChannel):
        async with self.session() as s:
            await s.merge(ldc)
            await s.commit()

    async def remove_lockdown(self, guild:Guild, channel:discord.TextChannel):
        async with self.session() as s:
            scalar = await s.exec(
                select(LockdownChannel)
                .where(LockdownChannel.guild_id == guild.id)
                .where(LockdownChannel.channel_id == channel.id)
                # selectinload is the important part otherwise no roles will be loaded and an error is thrown.
                .options(selectinload(LockdownChannel.roles))
            )
            ldc = scalar.one_or_none()

            for role in ldc.roles:
                if dsc_role := guild.get_role(role.role_id):
                    await channel.set_permissions(dsc_role, send_messages=True)
            await s.delete(ldc)
            await s.commit()
            
