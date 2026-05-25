"""
Copyright © Krypton 2019-Present - https://github.com/kkrypt0nn (https://krypton.ninja)
Description:
🐍 A simple template to start to code your own and personalized Discord bot in Python

Version: 6.5.0
"""

import asyncio
import hashlib
import hmac
import json
from typing import Optional

import aiohttp
from aiohttp import web
import discord
from discord.ext import commands
from discord.ext.commands import Context


class GitHubWebhook(commands.Cog, name="github_webhook"):
    def __init__(self, bot) -> None:
        self.bot = bot
        self.config = {}
        self.runner: Optional[aiohttp.web.AppRunner] = None
        self.site: Optional[aiohttp.web.TCPSite] = None

    async def cog_unload(self):
        """Clean up the webhook server when the cog is unloaded."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    @commands.hybrid_group(
        name="githubwebhook",
        description="Manage GitHub webhook settings.",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def githubwebhook(self, context: Context) -> None:
        """Manage GitHub webhook settings."""
        pass

    @githubwebhook.command(
        name="channel",
        description="Set the channel where GitHub updates will be posted.",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def channel(
        self, context: Context, channel: discord.TextChannel
    ) -> None:
        """Set the channel where GitHub updates will be posted."""
        guild_id = str(context.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["webhook_channel"] = channel.id
        await context.send(
            f"✅ GitHub updates will be posted to {channel.mention}"
        )

    @githubwebhook.command(
        name="secret",
        description="Set the GitHub webhook secret for security.",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def secret(
        self, context: Context, secret: str
    ) -> None:
        """Set the GitHub webhook secret for security."""
        guild_id = str(context.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["webhook_secret"] = secret
        await context.send("✅ GitHub webhook secret has been set.")

    @githubwebhook.command(
        name="port",
        description="Set the port for the webhook server (default: 8080).",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def port(
        self, context: Context, port: int
    ) -> None:
        """Set the port for the webhook server (default: 8080)."""
        if not (1 <= port <= 65535):
            return await context.send("❌ Port must be between 1 and 65535.")
        guild_id = str(context.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        self.config[guild_id]["webhook_port"] = port
        await context.send(f"✅ Webhook server port set to {port}")

    @githubwebhook.command(
        name="start",
        description="Start the webhook server for this guild.",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def start(self, context: Context) -> None:
        """Start the webhook server for this guild."""
        guild_id = str(context.guild.id)
        if guild_id not in self.config:
            self.config[guild_id] = {}
        
        channel_id = self.config[guild_id].get("webhook_channel")
        secret = self.config[guild_id].get("webhook_secret")
        port = self.config[guild_id].get("webhook_port", 8080)

        if not channel_id:
            return await context.send(
                "❌ Please set a channel first with `/githubwebhook channel #channel`"
            )

        if not secret:
            return await context.send(
                "❌ Please set a webhook secret first with `/githubwebhook secret your_secret`"
            )

        # Start the webhook server
        await self._start_webhook_server(port)
        await context.send(
            f"✅ Webhook server started on port {port}. "
            f"Configure your GitHub webhook to point to: "
            f"`http://YOUR_PUBLIC_IP:{port}/github/webhook/{context.guild.id}`"
        )

    @githubwebhook.command(
        name="stop",
        description="Stop the webhook server.",
    )
    @commands.has_permissions(manage_webhooks=True)
    async def stop(self, context: Context) -> None:
        """Stop the webhook server."""
        if self.site:
            await self.site.stop()
            self.site = None
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        await context.send("✅ Webhook server stopped.")

    async def _start_webhook_server(self, port: int):
        """Start the aiohttp webhook server."""
        app = web.Application()
        app.add_routes(
            [web.post("/github/webhook/{guild_id}", self._handle_webhook)]
        )
        
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "0.0.0.0", port)
        await self.site.start()

    async def _handle_webhook(self, request: web.Request):
        """Handle incoming GitHub webhook requests."""
        guild_id = int(request.match_info["guild_id"])
        guild = self.bot.get_guild(guild_id)
        
        if not guild:
            return web.Response(status=404, text="Guild not found")

        # Get guild-specific settings
        guild_id_str = str(guild_id)
        if guild_id_str not in self.config:
            return web.Response(status=400, text="Webhook not configured")
        
        channel_id = self.config[guild_id_str].get("webhook_channel")
        secret = self.config[guild_id_str].get("webhook_secret")

        if not channel_id or not secret:
            return web.Response(status=400, text="Webhook not configured")

        # Verify GitHub signature
        signature = request.headers.get("X-Hub-Signature-256")
        if not signature:
            return web.Response(status=403, text="No signature provided")

        body = await request.read()
        expected_signature = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            return web.Response(status=403, text="Invalid signature")

        # Parse the webhook payload
        try:
            payload = json.loads(body.decode())
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        # Handle push events
        if payload.get("ref_type") == "branch" or "ref" in payload:
            await self._process_push_event(guild, channel_id, payload)

        return web.Response(status=200, text="Webhook received")

    async def _process_push_event(
        self, guild: discord.Guild, channel_id: int, payload: dict
    ):
        """Process a GitHub push event and send to Discord."""
        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # Extract commit information
        pusher = payload.get("pusher", {}).get("name", "Unknown")
        repository = payload.get("repository", {}).get("full_name", "Unknown")
        ref = payload.get("ref", "Unknown")
        commits = payload.get("commits", [])
        compare_url = payload.get("compare", "")

        # Create embed
        embed = discord.Embed(
            title=f"🚀 New commits pushed to {repository}",
            description=f"Branch: `{ref.split('/')[-1]}`",
            color=0x2ea043,  # GitHub green
            url=compare_url,
        )

        embed.set_author(
            name=pusher,
            icon_url=payload.get("sender", {}).get("avatar_url"),
        )

        embed.set_thumbnail(
            url=payload.get("repository", {}).get("owner", {}).get("avatar_url")
        )

        # Add commit details
        if commits:
            commit_messages = []
            for commit in commits[:5]:  # Limit to 5 commits
                message = commit.get("message", "No message")
                author = commit.get("author", {}).get("name", "Unknown")
                url = commit.get("url", "")
                short_id = commit.get("id", "")[:7]
                
                commit_messages.append(
                    f"[`{short_id}`]({url}) {message} - {author}"
                )
            
            if len(commits) > 5:
                commit_messages.append(f"... and {len(commits) - 5} more commits")
            
            embed.add_field(
                name="Commits",
                value="\n".join(commit_messages),
                inline=False,
            )

        embed.set_footer(text=f"Total commits: {len(commits)}")
        embed.set_thumbnail(url="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png")

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass
        except Exception as e:
            print(f"Error sending embed: {e}")


async def setup(bot) -> None:
    await bot.add_cog(GitHubWebhook(bot))
