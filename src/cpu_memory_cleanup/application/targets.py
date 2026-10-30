"""范围规划；名称只展示，路径组不冒充完整多程序应用。"""

from dataclasses import asdict
import ntpath

from ..contracts import ProcessTarget

MAX_TARGETS = 4096


def path_key(value):
    if not isinstance(value, str) or not ntpath.isabs(value) or "\x00" in value:
        raise ValueError("保护与分组需要完整程序文件路径")
    return ntpath.normcase(ntpath.normpath(value))


def target_key(target):
    return target.pid, target.created_at


def targets_from_rows(rows):
    if not isinstance(rows, list) or len(rows) > MAX_TARGETS:
        raise ValueError("目标列表无效或超过批次上限")
    return tuple(ProcessTarget(**{k: row[k] for k in ("pid", "created_at", "name", "image_path") if k in row})
                 for row in rows)


def select_rows(rows, query="", *, descending=True):
    """返回新列表，选择状态由稳定身份持有，不随显示索引移动。"""
    query = query.casefold().strip()
    visible = [row for row in rows if query in f"{row.get('name', '')} {row['pid']} {row.get('image_path', '')}".casefold()]
    return sorted(visible, key=lambda row: (row.get("working_set_bytes") is not None,
                  row.get("working_set_bytes") or 0, row["pid"]), reverse=descending)


def executable_groups(rows):
    groups = {}
    for row in rows:
        target = targets_from_rows([row])[0]
        key = ("file", path_key(target.image_path)) if target.image_path else ("instance", *target_key(target))
        groups.setdefault(key, []).append(target)
    return list(groups.values())


def validate_rules(rules):
    if not isinstance(rules, list) or len(rules) > MAX_TARGETS:
        raise ValueError("保护规则列表无效")
    result = []
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("保护规则必须是对象")
        if set(rule) == {"image_path"}:
            result.append({"image_path": path_key(rule["image_path"])})
        elif set(rule) == {"pid", "created_at"}:
            target = ProcessTarget(**rule)
            result.append({"pid": target.pid, "created_at": target.created_at})
        else:
            raise ValueError("保护规则只能指定程序路径或完整进程身份")
    return result


def protected_reason(target, rules):
    for rule in rules:
        if "image_path" in rule:
            if not target.image_path:
                return "路径未知，无法排除受保护对象"
            if path_key(target.image_path) == rule["image_path"]:
                return "命中程序文件保护"
        elif target_key(target) == (rule["pid"], rule["created_at"]):
            return "命中进程实例保护"
    return ""


def plan_targets(targets, protections):
    if not isinstance(targets, (list, tuple)) or not 1 <= len(targets) <= MAX_TARGETS:
        raise ValueError("必须选择1..4096个目标")
    rules, seen, rows = validate_rules(protections), set(), []
    for target in targets:
        if not isinstance(target, ProcessTarget) or target_key(target) in seen:
            raise ValueError("目标必须有完整身份且不能重复")
        if target.image_path:
            path_key(target.image_path)
        seen.add(target_key(target))
        rows.append({"target": asdict(target), "reason": protected_reason(target, rules)})
    return rows, rules
