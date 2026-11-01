"""用户偏好合同，物理原子读写由系统层提供。"""

from .targets import validate_rules

def validate_preferences(value):
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("偏好版本无效")
    if set(value) - {"schema_version", "protections", "descending", "resident", "geometry", "rule"}:
        raise ValueError("未知偏好字段")
    result = {"schema_version": 1, "protections": validate_rules(value.get("protections", []))}
    for key in ("descending", "resident"):
        result[key] = value.get(key, key == "descending")
        if type(result[key]) is not bool:
            raise ValueError("偏好开关需要布尔值")
    geometry = value.get("geometry", "")
    import re
    if not isinstance(geometry, str) or (geometry and not re.fullmatch(r"\d+x\d+(?:[+-]\d+[+-]\d+)?", geometry)):
        raise ValueError("窗口位置无效")
    result["geometry"] = geometry
    if "rule" in value:
        from .policy import Rule
        from dataclasses import asdict
        result["rule"] = asdict(Rule(**value["rule"]))
    return result

