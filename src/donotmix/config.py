"""donotmix.yaml project configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class RunnerCfg(BaseModel):
    type: str = "command"  # command | python
    command: str = ""
    target: str = ""  # python runner: "module:function"
    cwd: str | None = None
    jobs: int = 8
    timeout: float = 120.0


class AnalysisCfg(BaseModel):
    paths: list[str] = Field(default_factory=lambda: ["."])
    incidents: str | None = ".donotmix/incidents.yaml"


class StrategyCfg(BaseModel):
    name: str = "risk"
    strength: int = 3
    budget: int | None = None
    seed: int = 0


class MinimizeCfg(BaseModel):
    exhaustive_limit: int = 128
    sample: int = 32
    global_limit: int = 12
    max_hazards_per_atom: int = 3
    max_tests: int = 4000


class AICfg(BaseModel):
    provider: str = "rule"  # rule | claude-cli | anthropic | none
    model: str = "claude-sonnet-5-5"
    max_calls: int = 6
    max_input_tokens: int = 24000
    max_output_tokens: int = 1200
    hypotheses: bool = True


class Persona(BaseModel):
    id: str
    identifier: str | None = None
    traits: dict[str, Any] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    project: str | None = None
    environment: str | None = None
    api_url: str | None = None
    flags: list[str] | None = None
    ignore_flags: list[str] = Field(default_factory=list)
    constraints: list[Any] = Field(default_factory=list)
    derive_segment_constraints: bool = False
    personas: list[Persona] = Field(default_factory=list)
    runner: RunnerCfg = Field(default_factory=RunnerCfg)
    analysis: AnalysisCfg = Field(default_factory=AnalysisCfg)
    strategy: StrategyCfg = Field(default_factory=StrategyCfg)
    minimize: MinimizeCfg = Field(default_factory=MinimizeCfg)
    ai: AICfg = Field(default_factory=AICfg)
    protected_environments: list[str] = Field(default_factory=lambda: ["Production", "production", "prod"])
    reference_environment: str | None = None

    @field_validator("strategy")
    @classmethod
    def _strength(cls, v: StrategyCfg):
        if v.strength < 1 or v.strength > 6:
            raise ValueError("strategy.strength must be between 1 and 6")
        return v

    root: Path = Path(".")


class ConfigError(ValueError):
    exit_code = 3


def load_config(path: str | Path = "donotmix.yaml") -> ProjectConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"{p} not found. Run `donotmix init` first.")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{p}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: expected a mapping at top level")
    try:
        cfg = ProjectConfig(**data)
    except Exception as e:  # pydantic.ValidationError
        raise ConfigError(f"{p}: {e}") from e
    cfg.root = p.resolve().parent
    return cfg


def load_incidents(cfg: ProjectConfig) -> list[dict]:
    if not cfg.analysis.incidents:
        return []
    p = cfg.root / cfg.analysis.incidents
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text()) or []
    return data if isinstance(data, list) else data.get("incidents", [])


TEMPLATE = """# DoNotMix configuration. Docs: https://github.com/dorakingx/donotmix
project: {project}
environment: {environment}          # environment to snapshot (read-only)
# api_url: http://localhost:8000    # self-hosted Flagsmith; omit for Flagsmith Cloud
protected_environments: [Production] # DoNotMix will never write to these

# flags: [...]                       # default: every flag in the environment
ignore_flags: []

constraints:                         # only feasible combinations are tested
  # - id: express-needs-checkout
  #   requires: [express_shipping, new_checkout]
  #   reason: express shipping is implemented in the new checkout only
  # - excludes: [guest_checkout, express_shipping]
  # - rule: "!(a & b) | c"

personas:                            # identity contexts, so segment overrides are evaluated
  - id: default
    identifier: donotmix-user
    traits: {{}}

runner:
  type: command
  # DONOTMIX_ENV_DOCUMENT points at a rewritten copy of the Flagsmith environment document;
  # run your Flagsmith SDK in offline / local-evaluation mode against it.
  command: "pytest -q -p no:cacheprovider --junitxml={{junit}}"
  jobs: 8

analysis:
  paths: [src]

strategy:
  name: risk          # exhaustive | random | tway | risk
  strength: 3
  seed: 0

ai:
  provider: rule      # rule (deterministic) | claude-cli | anthropic
  max_calls: 6
"""
