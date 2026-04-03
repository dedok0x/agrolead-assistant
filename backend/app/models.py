from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CompanyProfile(SQLModel, table=True):
    __tablename__ = "company_profile"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    address: str = ""
    phones: str = ""
    email: str = ""
    services: str = ""
    contacts_markdown: str = ""


class RefCommodity(SQLModel, table=True):
    __tablename__ = "ref_commodity"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str = Field(index=True)
    full_name: str = ""
    commodity_group: str = "grain"
    unit_of_measure_default: str = "тонна"
    is_active: bool = True
    sort_order: int = 100
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RefQualityParameter(SQLModel, table=True):
    __tablename__ = "ref_quality_parameter"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    value_type: str = "number"
    unit: str = ""
    is_active: bool = True
    sort_order: int = 100


class RefDeliveryBasis(SQLModel, table=True):
    __tablename__ = "ref_delivery_basis"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    is_active: bool = True


class RefTransportMode(SQLModel, table=True):
    __tablename__ = "ref_transport_mode"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    is_active: bool = True


class RefRegion(SQLModel, table=True):
    __tablename__ = "ref_region"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    country: str = "Россия"
    federal_district: str = ""
    region_name: str = ""
    city_name: str = ""
    port_name: str = ""
    is_active: bool = True


class RefLeadSource(SQLModel, table=True):
    __tablename__ = "ref_lead_source"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    channel_type: str = "web"
    is_active: bool = True


class RefCounterpartyType(SQLModel, table=True):
    __tablename__ = "ref_counterparty_type"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    is_active: bool = True


class RefRequestType(SQLModel, table=True):
    __tablename__ = "ref_request_type"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    is_active: bool = True


class RefPipelineStage(SQLModel, table=True):
    __tablename__ = "ref_pipeline_stage"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    pipeline_code: str = Field(index=True)
    sort_order: int = 100
    is_terminal: bool = False
    is_active: bool = True


class RefDepartment(SQLModel, table=True):
    __tablename__ = "ref_department"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    is_active: bool = True


class RefManagerRole(SQLModel, table=True):
    __tablename__ = "ref_manager_role"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    is_active: bool = True


class CatalogQualityTemplate(SQLModel, table=True):
    __tablename__ = "catalog_quality_template"
    id: Optional[int] = Field(default=None, primary_key=True)
    commodity_id: int = Field(index=True)
    template_code: str = Field(index=True, unique=True)
    template_name: str
    is_default: bool = False
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CatalogQualityTemplateLine(SQLModel, table=True):
    __tablename__ = "catalog_quality_template_line"
    id: Optional[int] = Field(default=None, primary_key=True)
    quality_template_id: int = Field(index=True)
    quality_parameter_id: int = Field(index=True)
    comparison_operator: str = "="
    target_value_numeric: Optional[float] = None
    target_value_text: str = ""
    sort_order: int = 100


class CatalogPricePolicy(SQLModel, table=True):
    __tablename__ = "catalog_price_policy"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    name: str
    commodity_id: Optional[int] = Field(default=None, index=True)
    request_type_id: Optional[int] = Field(default=None, index=True)
    source_region_id: Optional[int] = Field(default=None, index=True)
    destination_region_id: Optional[int] = Field(default=None, index=True)
    transport_mode_id: Optional[int] = Field(default=None, index=True)
    min_volume: Optional[float] = None
    max_volume: Optional[float] = None
    pricing_rule_text: str
    manager_note: str = ""
    is_active: bool = True
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None


class CatalogStockPlaceholder(SQLModel, table=True):
    __tablename__ = "catalog_stock_placeholder"
    id: Optional[int] = Field(default=None, primary_key=True)
    commodity_id: int = Field(index=True)
    quality_template_id: Optional[int] = Field(default=None, index=True)
    location_region_id: int = Field(index=True)
    volume_available: float = 0
    unit: str = "тонна"
    availability_status: str = "open"
    owner_label: str = ""
    transport_access_text: str = ""
    comment: str = ""
    is_active: bool = True
    updated_at: datetime = Field(default_factory=utcnow)


class CrmCounterparty(SQLModel, table=True):
    __tablename__ = "crm_counterparty"
    id: Optional[int] = Field(default=None, primary_key=True)
    counterparty_type_id: int = Field(index=True)
    company_name: str = ""
    contact_person: str = ""
    phone: str = ""
    email: str = ""
    telegram: str = ""
    inn: str = ""
    legal_form: str = ""
    region_id: Optional[int] = Field(default=None, index=True)
    comment: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CrmLead(SQLModel, table=True):
    __tablename__ = "crm_lead"
    id: Optional[int] = Field(default=None, primary_key=True)
    request_type_id: int = Field(index=True)
    source_id: int = Field(index=True)
    external_channel_session_id: str = Field(default="", index=True)
    counterparty_id: Optional[int] = Field(default=None, index=True)
    current_stage_id: int = Field(index=True)
    assigned_department_id: Optional[int] = Field(default=None, index=True)
    assigned_manager_user_id: Optional[int] = Field(default=None, index=True)
    status_code: str = Field(default="draft", index=True)
    priority_code: str = Field(default="normal", index=True)
    hot_flag: bool = False
    summary: str = ""
    next_action: str = ""
    manager_comment: str = ""
    raw_dialogue_compact: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    closed_at: Optional[datetime] = None


class CrmLeadItem(SQLModel, table=True):
    __tablename__ = "crm_lead_item"
    id: Optional[int] = Field(default=None, primary_key=True)
    lead_id: int = Field(index=True)
    commodity_id: Optional[int] = Field(default=None, index=True)
    quality_template_id: Optional[int] = Field(default=None, index=True)
    freeform_quality_text: str = ""
    volume_value: float = 0
    volume_unit: str = "тонна"
    source_region_id: Optional[int] = Field(default=None, index=True)
    destination_region_id: Optional[int] = Field(default=None, index=True)
    delivery_basis_id: Optional[int] = Field(default=None, index=True)
    transport_mode_id: Optional[int] = Field(default=None, index=True)
    target_price: Optional[float] = None
    planned_date_from: Optional[datetime] = None
    planned_date_to: Optional[datetime] = None
    export_flag: bool = False
    comment: str = ""


class CrmLeadContactSnapshot(SQLModel, table=True):
    __tablename__ = "crm_lead_contact_snapshot"
    id: Optional[int] = Field(default=None, primary_key=True)
    lead_id: int = Field(index=True)
    company_name: str = ""
    contact_name: str = ""
    phone: str = ""
    email: str = ""
    telegram: str = ""
    comment: str = ""


class CrmLeadDocumentRequest(SQLModel, table=True):
    __tablename__ = "crm_lead_document_request"
    id: Optional[int] = Field(default=None, primary_key=True)
    lead_id: int = Field(index=True)
    doc_type: str
    doc_status: str = "requested"
    requested_at: datetime = Field(default_factory=utcnow)
    received_at: Optional[datetime] = None
    comment: str = ""


class CrmTask(SQLModel, table=True):
    __tablename__ = "crm_task"
    id: Optional[int] = Field(default=None, primary_key=True)
    lead_id: int = Field(index=True)
    task_type: str = "follow_up"
    title: str
    description: str = ""
    due_at: Optional[datetime] = None
    status: str = Field(default="open", index=True)
    assigned_user_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_session"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(index=True)
    external_user_id: Optional[str] = Field(default=None, index=True)
    external_chat_id: Optional[str] = Field(default=None, index=True)
    request_type_id: Optional[int] = Field(default=None, index=True)
    lead_id: Optional[int] = Field(default=None, index=True)
    current_state_code: str = Field(default="new", index=True)
    language_code: str = "ru"
    last_user_message_at: Optional[datetime] = None
    last_bot_message_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_message"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    direction: str = Field(index=True)
    text: str
    message_type: str = "text"
    blocked: bool = False
    block_reason: str = ""
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ChatExtractedFact(SQLModel, table=True):
    __tablename__ = "chat_extracted_fact"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    lead_id: Optional[int] = Field(default=None, index=True)
    fact_key: str = Field(index=True)
    fact_value_text: str = ""
    fact_value_numeric: Optional[float] = None
    fact_value_date: Optional[datetime] = None
    confidence: float = 0.5
    source_message_id: Optional[int] = Field(default=None, index=True)
    is_confirmed: bool = False
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChatMissingField(SQLModel, table=True):
    __tablename__ = "chat_missing_field"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    lead_id: Optional[int] = Field(default=None, index=True)
    field_code: str = Field(index=True)
    priority_order: int = 100
    is_required: bool = True
    is_collected: bool = False
    asked_count: int = 0
    last_asked_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class ChatQualificationCheckpoint(SQLModel, table=True):
    __tablename__ = "chat_qualification_checkpoint"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    lead_id: Optional[int] = Field(default=None, index=True)
    checkpoint_code: str = Field(index=True)
    checkpoint_status: str = "ok"
    note: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_user"
    id: Optional[int] = Field(default=None, primary_key=True)
    login: str = Field(index=True, unique=True)
    password_hash: str
    full_name: str
    role_code: str = Field(index=True)
    department_id: Optional[int] = Field(default=None, index=True)
    is_active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AdminSetting(SQLModel, table=True):
    __tablename__ = "admin_setting"
    id: Optional[int] = Field(default=None, primary_key=True)
    setting_key: str = Field(index=True, unique=True)
    setting_value: str = ""
    setting_group: str = "general"
    description: str = ""
    is_secret: bool = False
    updated_at: datetime = Field(default_factory=utcnow)


class KnowledgeArticle(SQLModel, table=True):
    __tablename__ = "knowledge_article"
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    title: str
    article_group: str = Field(index=True)
    request_type_id: Optional[int] = Field(default=None, index=True)
    commodity_id: Optional[int] = Field(default=None, index=True)
    content_markdown: str
    short_answer: str = ""
    is_active: bool = True
    sort_order: int = 100
    updated_at: datetime = Field(default_factory=utcnow)
