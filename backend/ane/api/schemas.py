"""Pydantic models for API requests and responses."""

from pydantic import BaseModel, Field


# ── Auth ────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-zA-Z0-9_一-鿿]+$")
    password: str = Field(..., min_length=4, max_length=100)
    display_name: str = Field(default="", max_length=50)
    is_adult: bool = Field(default=False)

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class AuthResponse(BaseModel):
    token: str
    user_id: str
    username: str
    display_name: str = ""
    is_adult: bool = False


# ── Session ──────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    name: str = Field(default="未命名世界", max_length=100)


class CreateSessionResponse(BaseModel):
    session_id: str
    name: str
    world_time: str
    player_name: str
    player_location: str
    player_cultivation: str = ""
    region_count: int


class ConversationEntry(BaseModel):
    turn_number: int
    content: str


class SessionSummary(BaseModel):
    session_id: str
    name: str
    world_time: str
    is_active: bool
    created_at: str | None = None
    player_name: str = ""
    player_cultivation: str = ""
    player_location: str = ""
    player_gender: str = ""
    travel_log: list[dict] = []
    conversation: list[ConversationEntry] = []
    npc_names: list[str] = []
    htem_directory: str = ""
    map_data: dict | None = None
    world_intro: str = ""
    prompts: list[ConversationEntry] = []
    recommendations: list[str] = []


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool


# ── Turn ─────────────────────────────────────────────────────

class TurnRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=4000, description="玩家输入")
    model: str | None = Field(default=None, description="模型标识，如 openai:gpt-4o")
    mark_important_npc: bool = Field(default=False, description="用户勾选了'重要人物'按钮")
    load_model_data: bool = Field(default=True, description="是否自动加载已建模人物数据")


class TurnResponse(BaseModel):
    narrative: str
    state_changes: list[dict]
    world_time: str
    time_delta: int
    npc_updates: list[dict]
    nearby_characters: list[dict] = []
    htem_directory: str = ""
    is_system_command: bool = False
    system_response: str | None = None
    prompt: str = ""
    player_panel: str = ""
    important_npcs_panel: str = ""
    modeled_npcs: list[dict] = []
    recommendations: list[str] = []


# ── Character Creation ─────────────────────────────────────────

class ApplyCharacterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=20, description="玩家姓名")
    age: int = Field(default=19, ge=12, le=999, description="年龄")
    gender: str = Field(default="男", description="性别：男/女")
    background: str = Field(default="无父无母", description="出身背景")
    cultivation: str = Field(default="凡人", description="修为选择")
    personality: str = Field(default="谨慎隐忍", description="性格选择")
    personality_custom: str = Field(default="", max_length=500, description="自定义性格描述")
    identity: str = Field(default="外门弟子", description="宗门身份选择")
    golden_finger_id: str = Field(default="", description="金手指类别 ID")
    golden_finger_custom: str = Field(default="", max_length=500, description="金手指自定义描写")
    identity_custom: str = Field(default="", max_length=300, description="自定义身份描述")
    chosen_sect: str = Field(default="", description="选择的初始宗门")

# ── Map Move ───────────────────────────────────────────────────

class MoveRequest(BaseModel):
    destination: str = Field(..., min_length=1, max_length=100, description="目标城市/宗门名")
    dest_x: float = Field(..., description="目标 X 坐标")
    dest_y: float = Field(..., description="目标 Y 坐标")


# ── NPC Modeling ───────────────────────────────────────────────────

class NpcModelingRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=4000, description="角色描述输入")


class NpcModelingResponse(BaseModel):
    updated: list[dict] = Field(default_factory=list, description="已更新档案的NPC列表 [{npc_name, model_data}]")
    new_names: list[str] = Field(default_factory=list, description="待确认建模的新NPC姓名列表")


class NpcModelingConfirmRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=4000, description="角色描述输入（同一段原始输入）")
    name: str = Field(..., min_length=1, max_length=20, description="要建模的人物姓名")


class NpcModelingConfirmResponse(BaseModel):
    npc_name: str
    model_data: dict


# ── Summaries (📕) ─────────────────────────────────────────────────

class SummaryEntry(BaseModel):
    turn_number: int
    content: str
    created_at: str | None = None


class SummariesResponse(BaseModel):
    session_id: str
    from_turn: int
    summaries: list[SummaryEntry]

class MemoryResponse(BaseModel):
    short: list[SummaryEntry] = []
    long: list[SummaryEntry] = []


# ── NPC Library (跨世界总库) ─────────────────────────────────

class NpcLibraryCreateRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=4000, description="角色描述输入")
    tags: list[str] = Field(default_factory=list, description="用户自定义标签")

class NpcLibraryUpdateRequest(BaseModel):
    input: str = Field(default="", max_length=4000, description="角色描述输入（AI增量更新）")
    model_data: dict | None = Field(default=None, description="完整mod el_data直接替换（AI增量更新时留空）")
    tags: list[str] | None = None

class NpcLibraryEntry(BaseModel):
    name: str
    model_data: dict = {}
    tags: list[str] = []


class NpcLibraryResponse(BaseModel):
    npcs: list[NpcLibraryEntry]
