import asyncio
import json
import discord
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
from openai import AsyncOpenAI

def load_key(filename: str) -> str:
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"Error: {filename} not found. Please create it and add your token/key.")
        exit(1)

DISCORD_TOKEN = load_key("token.txt")
OPENROUTER_API_KEY = load_key("api.txt")

ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY
)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


class PromptModal(Modal, title="Modify Generation"):
    feedback = TextInput(
        label="What would you like to change?",
        style=discord.TextStyle.paragraph,
        placeholder="e.g., Add more gaming channels, change role colors...",
        required=True
    )

    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.custom_changes = self.feedback.value
        self.parent_view.user_choice = "change"
        self.parent_view.stop()
        await interaction.response.defer()


class ConfirmView(View):
    def __init__(self, author, allow_change=True):
        super().__init__(timeout=180)
        self.author = author
        self.user_choice = None
        self.custom_changes = ""

        if not allow_change:
            self.remove_item(self.children[2])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Only the command user can interact with this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: Button):
        self.user_choice = "yes"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: Button):
        self.user_choice = "no"
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Change More", style=discord.ButtonStyle.gray)
    async def change_button(self, interaction: discord.Interaction, button: Button):
        modal = PromptModal(self)
        await interaction.response.send_modal(modal)


async def ask_ai(system_instructions: str, user_prompt: str) -> list:
    try:
        response = await ai_client.chat.completions.create(
            model="openrouter/free",
            messages=[
                {"role": "system", "content": system_instructions},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            extra_headers={
                "HTTP-Referer": "https://discord-bot-local",
                "X-Title": "Discord Setup Bot"
            }
        )
        
        raw_text = response.choices[0].message.content.strip()
        
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[-1].rsplit("\n", 1)[0]
        if raw_text.lower().startswith("json"):
            raw_text = raw_text[4:].strip()
                
        return json.loads(raw_text)

    except Exception as e:
        print(f"API Error: {e}")
        return []


@bot.event
async def on_ready():
    print(f"Bot connected as {bot.user}")


@bot.command(name="setup")
@commands.has_permissions(administrator=True)
async def setup_server(ctx: commands.Context):
    bot_perms = ctx.guild.me.guild_permissions
    if not (bot_perms.manage_channels and bot_perms.manage_roles):
        return await ctx.send("Error: Bot requires `Manage Channels` and `Manage Roles` permissions.")

    def check_msg(m):
        return m.author == ctx.author and m.channel == ctx.channel

    embed_ch = discord.Embed(
        title="Server Setup Assistant",
        description="Add channels (be specific with your server theme or required topics):",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed_ch)

    try:
        msg = await bot.wait_for("message", check=check_msg, timeout=120)
        channel_desc = msg.content
    except asyncio.TimeoutError:
        return await ctx.send("Command timed out. Run `!setup` again.")

    system_channel_instructions = """
    You are an expert Discord server architect. The user will specify a theme.
    You MUST generate a MASSIVE, highly detailed, and complete Discord server structure. Do not hold back—create MANY categories (at least 6-8 categories) and MANY channels per category (at least 4-6 channels per category) so the server feels fully built out.
    Include thematic emojis directly inside the channel names (e.g., "📔-rules", "💬-general-chat", "🔊-chill-lounge").
    Respond ONLY with a valid JSON array of categories and channels. No intro text, no markdown code blocks outside raw JSON text if possible, just the raw JSON structure.
    Format exactly like this:
    [
        {
            "category": "📌 | INFORMATION",
            "channels": [
                {"name": "📔-rules", "type": "text", "media_only": false},
                {"name": "📢-announcements", "type": "text", "media_only": false},
                {"name": "🤖-bot-commands", "type": "text", "media_only": true}
            ]
        }
    ]
    """

    channels_data = []
    while True:
        status_msg = await ctx.send("Drafting channel layout...")
        channels_data = await ask_ai(system_channel_instructions, channel_desc)
        await status_msg.delete()

        if not channels_data:
            return await ctx.send("Failed to generate valid channel data. Please run `!setup` again.")

        preview_text = ""
        for cat in channels_data:
            preview_text += f"**[{cat.get('category', 'Category')}]**\n"
            for ch in cat.get('channels', []):
                prefix = "Voice: " if ch.get("type") == "voice" else ""
                preview_text += f"{prefix}{ch.get('name', 'channel')}\n"
            preview_text += "\n"

        # Truncate preview if too long for discord embed description limit
        if len(preview_text) > 3900:
            preview_text = preview_text[:3900] + "\n...[List truncated for length, but will fully build]"

        view = ConfirmView(ctx.author)
        embed_preview = discord.Embed(
            title="Channel Layout Preview",
            description=f"Are these channels good?\n\n{preview_text}",
            color=discord.Color.red()
        )
        confirm_msg = await ctx.send(embed=embed_preview, view=view)
        await view.wait()

        if view.user_choice == "yes":
            break
        elif view.user_choice == "no":
            return await ctx.send("Server setup canceled.")
        elif view.user_choice == "change":
            channel_desc += f"\n\nMake these changes to the layout: {view.custom_changes}"
            await confirm_msg.delete()

    embed_perms = discord.Embed(
        title="Channel Permissions",
        description="Would you like to take away permissions such as threads or media for non-media channels? (You can change them later in server settings)",
        color=discord.Color.red()
    )
    view_perms = ConfirmView(ctx.author, allow_change=False)
    await ctx.send(embed=embed_perms, view=view_perms)
    await view_perms.wait()
    restrict_non_media = (view_perms.user_choice == "yes")

    embed_roles = discord.Embed(
        title="Roles Setup",
        description="What roles would you like to have?",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed_roles)

    try:
        msg = await bot.wait_for("message", check=check_msg, timeout=120)
        role_desc = msg.content
    except asyncio.TimeoutError:
        return await ctx.send("Command timed out.")

    system_role_instructions = """
    You are an expert Discord server architect. The user will tell you what roles they want.
    You MUST generate a MASSIVE, complete, and hierarchical list of server roles (generate at least 12-18 roles covering Owners, High Staff, Moderation, Content Creators, Server Boosters, Bots, VIPs, and multiple community member tiers) with fitting custom hex colors.
    Respond ONLY with a valid JSON array. No intro text, no markdown.
    Format exactly like this:
    [
        {"name": "Owner", "color": "#FF0000", "admin": true},
        {"name": "Developer", "color": "#0055FF", "admin": true},
        {"name": "Head Moderator", "color": "#FFAA00", "admin": false},
        {"name": "Moderator", "color": "#FFCC00", "admin": false},
        {"name": "Server Booster", "color": "#FF73FA", "admin": false},
        {"name": "VIP", "color": "#9b59b6", "admin": false},
        {"name": "Active Member", "color": "#3498db", "admin": false},
        {"name": "Member", "color": "#2ecc71", "admin": false},
        {"name": "Bot", "color": "#95a5a6", "admin": false}
    ]
    """

    roles_data = []
    while True:
        status_msg = await ctx.send("Drafting role structure...")
        roles_data = await ask_ai(system_role_instructions, role_desc)
        await status_msg.delete()

        if not roles_data:
             return await ctx.send("Failed to generate valid role data. Please run `!setup` again.")

        roles_preview = "\n".join([f"@ {r.get('name', 'Role')}" for r in roles_data])
        if len(roles_preview) > 3900:
            roles_preview = roles_preview[:3900] + "\n..."

        view = ConfirmView(ctx.author)
        embed_roles_preview = discord.Embed(
            title="Roles Preview",
            description=f"Are these roles good?\n\n{roles_preview}",
            color=discord.Color.red()
        )
        confirm_msg = await ctx.send(embed=embed_roles_preview, view=view)
        await view.wait()

        if view.user_choice == "yes":
            break
        elif view.user_choice == "no":
            return await ctx.send("Server setup canceled.")
        elif view.user_choice == "change":
            role_desc += f"\n\nMake these changes to the roles: {view.custom_changes}"
            await confirm_msg.delete()

    embed_admin = discord.Embed(
        title="Role Permissions",
        description="Would you like these roles to also have the permissions of the roles, such as @Owner having full admin permissions?",
        color=discord.Color.red()
    )
    view_admin = ConfirmView(ctx.author, allow_change=False)
    await ctx.send(embed=embed_admin, view=view_admin)
    await view_admin.wait()
    apply_admin_perms = (view_admin.user_choice == "yes")

    embed_final = discord.Embed(
        title="Ready to Build",
        description="The draft is complete. Are you ready to create all channels and roles?",
        color=discord.Color.red()
    )
    view_final = ConfirmView(ctx.author, allow_change=False)
    await ctx.send(embed=embed_final, view=view_final)
    await view_final.wait()
    
    if view_final.user_choice != "yes":
        return await ctx.send("Server build canceled. Nothing was created.")

    build_msg = await ctx.send("Building server\n`[----------] 0% done`")

    async def update_progress(percent, stage):
        bars = int(percent // 10)
        progress_bar = "=" * bars + "-" * (10 - bars)
        try:
            await build_msg.edit(content=f"Building server - {stage}\n`[{progress_bar}] {percent}% done`")
        except discord.HTTPException:
            pass 

    guild = ctx.guild

    await update_progress(20, "Creating Roles")
    for r in roles_data:
        try:
            color_val = int(str(r.get("color", "#99AAB5")).lstrip("#"), 16)
        except ValueError:
            color_val = 0x99AAB5

        perms = discord.Permissions.all() if (apply_admin_perms and r.get("admin", False)) else discord.Permissions.none()

        try:
            await guild.create_role(
                name=r.get("name", "New Role"),
                color=discord.Color(color_val),
                permissions=perms,
                hoist=True,
                mentionable=True
            )
        except Exception as e:
            print(f"Skipped a role due to error: {e}")
        await asyncio.sleep(0.3)

    await update_progress(50, "Creating Channels")
    total_cats = len(channels_data)
    for i, cat in enumerate(channels_data):
        try:
            category = await guild.create_category(cat.get("category", "New Category"))
            for ch in cat.get("channels", []):
                if ch.get("type") == "voice":
                    await guild.create_voice_channel(ch.get("name", "voice-chat"), category=category)
                else:
                    overwrites = {}
                    if restrict_non_media and not ch.get("media_only", False):
                        overwrites[guild.default_role] = discord.PermissionOverwrite(
                            attach_files=False,
                            create_public_threads=False,
                            create_private_threads=False
                        )
                    await guild.create_text_channel(ch.get("name", "text-channel"), category=category, overwrites=overwrites)
                await asyncio.sleep(0.3)
        except Exception as e:
             print(f"Skipped a category/channel due to error: {e}")

        progress = 50 + int(((i + 1) / total_cats) * 45)
        await update_progress(progress, f"Structuring {cat.get('category', '')}")

    await update_progress(100, "Completed")
    await ctx.send("Server setup complete.")

    embed_sd = discord.Embed(
        title="Self Destruct",
        description="Would you like the bot to self-destruct (ban itself) now that the server build is complete?",
        color=discord.Color.red()
    )
    view_sd = ConfirmView(ctx.author, allow_change=False)
    await ctx.send(embed=embed_sd, view=view_sd)
    await view_sd.wait()

    if view_sd.user_choice == "yes":
        await ctx.send("Initiating self-destruct...")
        await asyncio.sleep(1) 
        try:
            await guild.ban(guild.me, reason="Bot self-destruct requested by user.")
        except discord.Forbidden:
            await ctx.send("Lacking permission to ban myself. Leaving the server instead...")
            await guild.leave()


bot.run(DISCORD_TOKEN)