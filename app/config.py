from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_timezone: str = "Asia/Shanghai"
    database_path: str = "data/health.db"

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_default_receive_id: str = ""
    feishu_default_receive_id_type: str = "open_id"
    feishu_event_mode: str = "http"

    user_name: str = "我"
    health_primary_goal: str = "保持规律作息，稳步提升饮水、饮食均衡、睡眠和运动习惯"
    health_goal_constraints: str = "以可持续为优先，不做激进节食或超负荷运动"
    wake_time: str = "07:30"
    sleep_time: str = "23:00"
    water_daily_goal_ml: int = 2000
    water_remind_start: str = "08:30"
    water_remind_end: str = "21:30"
    water_remind_check_minutes: int = 30
    water_remind_min_interval_minutes: int = 90
    meal_breakfast_time: str = "08:00"
    meal_lunch_time: str = "12:00"
    meal_dinner_time: str = "18:30"
    exercise_monthly_goal: int = 12
    health_memory_summary_time: str = "22:30"
    health_memory_lookback_days: int = 7

    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_api_style: str = "chat_completions"
    llm_timeout_seconds: float = 45.0
    llm_context_limit: int = 12


settings = Settings()
