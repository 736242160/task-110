#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relation_checker.py — 多关系一致性仲裁器（纯 Python 标准库，单文件）

用法：
    python3 relation_checker.py 输入文件        # 校验指定文件
    python3 relation_checker.py < 输入文件      # 从标准输入读取
    python3 relation_checker.py --demo          # 运行内置演示样例
    python3 relation_checker.py --selftest      # 运行内置自测

输入格式（UTF-8 文本；# 之后为注释；空行忽略）：

    entity 名称                 定义实体（每行一个）
    pair A B                    定义配对关系的一半（需反向 pair B A 闭环）
    ref  A B                    定义引用关系（A 引用 B，B 必须是已定义实体）
    map  A B                    定义映射关系的一半（需反向 map B A 闭环，且双向唯一）
    delete pair A B             删除已定义的关系（pair / ref / map 均可删除）

校验规则：
    1. 同类内部一致：配对成对、引用目标存在、映射双向唯一
    2. 跨类交叉：同一实体同时出现在配对与映射中且约束冲突 -> 报告实体与两类关系
    3. 关系内容引用不存在的实体
    4. 关系重复定义
    5. 跨类环：报告环上实体链
    6. 关系删除后残留：引用已删关系
    7. 跨行状态延续：输入结束仍未闭环的配对 / 映射
"""

import sys
from collections import defaultdict

REL_TYPES = ("pair", "ref", "map")
TYPE_CN = {"pair": "配对", "ref": "引用", "map": "映射"}
MAX_CYCLE_LEN = 12   # 环搜索的最大长度，防止爆炸
MAX_CYCLES = 20      # 最多报告的环数量


class Relation:
    __slots__ = ("rtype", "a", "b", "line")

    def __init__(self, rtype, a, b, line):
        self.rtype = rtype
        self.a = a
        self.b = b
        self.line = line

    def key(self):
        return (self.rtype, self.a, self.b)

    def __str__(self):
        return "%s %s %s" % (self.rtype, self.a, self.b)


class Error:
    def __init__(self, code, line, message):
        self.code = code
        self.line = line          # 0 表示“输入结束时”才确定的问题
        self.message = message

    def __str__(self):
        where = "行 %-4d" % self.line if self.line else "输入结束"
        return "[%s] %-18s %s" % (where, self.code, self.message)


# ---------------------------------------------------------------- 解析

def parse(text):
    """返回 (entities, events, errors)。
    entities: 名称 -> 定义行号
    events:   [("add"|"delete", Relation), ...] 按出现顺序
    """
    entities = {}
    events = []
    errors = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        cmd = parts[0]
        if cmd == "entity":
            if len(parts) != 2:
                errors.append(Error("SYNTAX", lineno,
                    "entity 需要 1 个名称参数: %r" % raw.strip()))
                continue
            name = parts[1]
            if name in entities:
                errors.append(Error("DUPLICATE_ENTITY", lineno,
                    "实体重复定义: %s（首次定义于行 %d）" % (name, entities[name])))
            else:
                entities[name] = lineno
        elif cmd in REL_TYPES:
            if len(parts) != 3:
                errors.append(Error("SYNTAX", lineno,
                    "%s 需要 2 个实体参数: %r" % (cmd, raw.strip())))
                continue
            events.append(("add", Relation(cmd, parts[1], parts[2], lineno)))
        elif cmd == "delete":
            if len(parts) != 4 or parts[1] not in REL_TYPES:
                errors.append(Error("SYNTAX", lineno,
                    "delete 用法: delete <pair|ref|map> A B: %r" % raw.strip()))
                continue
            events.append(("delete", Relation(parts[1], parts[2], parts[3], lineno)))
        else:
            errors.append(Error("SYNTAX", lineno,
                "无法识别的指令: %r" % raw.strip()))
    return entities, events, errors


# ---------------------------------------------------------------- 校验

def check(text):
    """对输入文本执行全部校验，返回 Error 列表（按行号排序）。"""
    entities, events, errors = parse(text)

    active = {}    # (rtype, a, b) -> Relation，当前存活的关系
    deleted = []   # 被删除的 Relation（line 为删除行号）

    # 按流顺序应用事件：重复定义、删除未知关系在此发现
    for op, rel in events:
        if op == "add":
            if rel.key() in active:
                errors.append(Error("DUPLICATE_RELATION", rel.line,
                    "关系重复定义: %s（首次定义于行 %d）"
                    % (rel, active[rel.key()].line)))
            else:
                active[rel.key()] = rel
        else:
            if rel.key() in active:
                del active[rel.key()]
                deleted.append(rel)
            else:
                errors.append(Error("DELETE_UNKNOWN", rel.line,
                    "删除不存在（或已被删除）的关系: %s" % rel))

    # 规则 3：关系内容引用不存在的实体
    reported_undef = set()
    for _op, rel in events:
        for name in (rel.a, rel.b):
            if name not in entities and (rel.line, name) not in reported_undef:
                reported_undef.add((rel.line, name))
                errors.append(Error("UNDEFINED_ENTITY", rel.line,
                    "关系 %s 引用了不存在的实体: %s" % (rel, name)))

    live = sorted(active.values(), key=lambda r: r.line)

    # 规则 1a + 7：配对成对 / 映射双向闭环（跨行状态延续到输入结束仍未闭环）
    for rel in live:
        if rel.rtype == "pair" and ("pair", rel.b, rel.a) not in active:
            errors.append(Error("UNCLOSED_PAIR", rel.line,
                "配对未闭环: pair %s %s 缺少反向定义 pair %s %s（状态延续至输入结束）"
                % (rel.a, rel.b, rel.b, rel.a)))
        if rel.rtype == "map" and ("map", rel.b, rel.a) not in active:
            errors.append(Error("UNCLOSED_MAP", rel.line,
                "映射未闭环: map %s %s 缺少反向定义 map %s %s（状态延续至输入结束）"
                % (rel.a, rel.b, rel.b, rel.a)))

    # 规则 1c：映射双向唯一（一对一）
    fwd = defaultdict(dict)   # src -> {dst: line}
    bwd = defaultdict(dict)   # dst -> {src: line}
    for rel in live:
        if rel.rtype == "map":
            fwd[rel.a][rel.b] = rel.line
            bwd[rel.b][rel.a] = rel.line
    for src, targets in sorted(fwd.items()):
        if len(targets) > 1:
            errors.append(Error("MAPPING_NOT_UNIQUE", min(targets.values()),
                "映射前向不唯一: %s 同时映射到 %s"
                % (src, "、".join(sorted(targets)))))
    for dst, sources in sorted(bwd.items()):
        if len(sources) > 1:
            errors.append(Error("MAPPING_NOT_UNIQUE", min(sources.values()),
                "映射反向不唯一: %s 同时被 %s 映射"
                % (dst, "、".join(sorted(sources)))))

    # 规则 2：跨类交叉 —— 同一实体同时出现在配对与映射中且约束冲突
    pair_partners = defaultdict(set)
    map_partners = defaultdict(set)
    for rel in live:
        if rel.rtype == "pair":
            pair_partners[rel.a].add(rel.b)
        elif rel.rtype == "map":
            map_partners[rel.a].add(rel.b)
            map_partners[rel.b].add(rel.a)   # 映射闭环后视为双向关联
    for name in sorted(set(pair_partners) & set(map_partners)):
        if pair_partners[name] != map_partners[name]:
            errors.append(Error("CROSS_TYPE_CONFLICT", 0,
                "实体 %s 同时出现在配对(pair)与映射(map)中且约束冲突: "
                "配对对象 %s != 映射对象 %s"
                % (name, sorted(pair_partners[name]), sorted(map_partners[name]))))

    # 规则 6：关系删除后残留 —— 存活的引用指向已被删除的配对/映射
    for rel in live:
        if rel.rtype != "ref":
            continue
        for d in deleted:
            if d.line <= rel.line:
                continue
            if d.rtype in ("pair", "map") and {d.a, d.b} == {rel.a, rel.b}:
                errors.append(Error("RESIDUAL_REFERENCE", rel.line,
                    "引用残留: ref %s %s 仍然存活，但其指向的 %s %s %s 已于行 %d 被删除"
                    % (rel.a, rel.b, d.rtype, d.a, d.b, d.line)))

    # 规则 5：跨类环（引用 + 映射构成的有向图中，含两类以上边的环）
    edges = defaultdict(list)   # src -> [(dst, rtype)]
    for rel in live:
        if rel.rtype in ("ref", "map"):
            edges[rel.a].append((rel.b, rel.rtype))

    cycles = {}

    def dfs(start, node, path, types):
        for nxt, rtype in edges.get(node, ()):
            if nxt == start and len(path) >= 2:
                all_types = types | {rtype}
                if len(all_types) >= 2:
                    cyc = tuple(path)
                    canon = min(cyc[i:] + cyc[:i] for i in range(len(cyc)))
                    cycles.setdefault(canon, all_types)
            elif nxt not in path and len(path) < MAX_CYCLE_LEN:
                dfs(start, nxt, path + [nxt], types | {rtype})

    for node in sorted(edges):
        dfs(node, node, [node], set())
    for canon, types in sorted(cycles.items())[:MAX_CYCLES]:
        chain = " -> ".join(list(canon) + [canon[0]])
        errors.append(Error("CROSS_TYPE_CYCLE", 0,
            "检测到跨类环（%s）: %s"
            % ("+".join(TYPE_CN[t] for t in sorted(types)), chain)))

    errors.sort(key=lambda e: (e.line == 0, e.line, e.code))
    return errors


# ---------------------------------------------------------------- 报告

def report(errors):
    lines = []
    if errors:
        lines.append("校验结果: 失败（%d 个错误）" % len(errors))
        lines.append("错误清单:")
        for i, e in enumerate(errors, 1):
            lines.append("  %2d. %s" % (i, e))
    else:
        lines.append("校验结果: 通过（0 个错误）")
    return "\n".join(lines)


# ---------------------------------------------------------------- 演示样例

DEMO = """\
# ---- 实体定义 ----
entity Alice
entity Bob
entity Carol
entity Dave
entity Erin

# ---- 正常关系 ----
pair Alice Bob        # 配对的两半
pair Bob Alice
ref  Erin Alice       # 引用已定义实体，合法
ref  Alice Bob        # 指向 pair Alice Bob 的引用（下方删除配对后成为残留）

# ---- 问题关系 ----
pair Carol Dave       # 配对未闭环：缺少 pair Dave Carol
map  Alice Carol      # 映射的两半
map  Carol Alice
map  Alice Dave       # 映射前向不唯一：Alice -> Carol 且 Alice -> Dave
map  Dave Alice
ref  Bob Carol        # 与 map Carol Alice 构成跨类环 Carol->Alice? 见下
map  Carol Bob        # 跨类环边之一（未闭环，顺带触发 UNCLOSED_MAP）
ref  Alice Ghost      # 引用不存在的实体
ref  Erin Alice       # 重复定义
delete pair Alice Bob # 删除配对的一半
delete pair Bob Alice # 另一半也被删除 -> 下方引用成为残留
delete map  Alice Bob # 删除从未定义的关系
"""


# ---------------------------------------------------------------- 自测

def selftest():
    cases = [
        ("全部合法（应通过）",
         "entity A\nentity B\nentity C\nentity D\nentity E\n"
         "pair A B\npair B A\nmap C D\nmap D C\nref E A\n",
         set(), 0),

        ("配对未闭环",
         "entity A\nentity B\npair A B\n",
         {"UNCLOSED_PAIR"}, None),

        ("映射未闭环",
         "entity A\nentity B\nmap A B\n",
         {"UNCLOSED_MAP"}, None),

        ("引用不存在的实体",
         "entity A\nref A X\n",
         {"UNDEFINED_ENTITY"}, None),

        ("关系重复定义",
         "entity A\nentity B\nref A B\nref A B\n",
         {"DUPLICATE_RELATION"}, None),

        ("映射前向不唯一",
         "entity A\nentity B\nentity C\n"
         "map A B\nmap B A\nmap A C\nmap C A\n",
         {"MAPPING_NOT_UNIQUE"}, None),

        ("映射反向不唯一",
         "entity A\nentity B\nentity C\n"
         "map A C\nmap C A\nmap B C\nmap C B\n",
         {"MAPPING_NOT_UNIQUE"}, None),

        ("跨类交叉冲突（配对 vs 映射）",
         "entity A\nentity B\nentity C\n"
         "pair A B\npair B A\nmap A C\nmap C A\n",
         {"CROSS_TYPE_CONFLICT"}, None),

        ("配对与映射对象一致（不冲突）",
         "entity A\nentity B\npair A B\npair B A\nmap A B\nmap B A\n",
         set(), 0),

        ("跨类环（映射+引用）",
         "entity A\nentity B\nmap A B\nref B A\n",
         {"CROSS_TYPE_CYCLE"}, None),

        ("同类环不算跨类环",
         "entity A\nentity B\nentity C\nref A B\nref B C\nref C A\n",
         set(), 0),

        ("删除后引用残留",
         "entity A\nentity B\nentity C\n"
         "ref A B\npair A B\npair B A\n"
         "delete pair A B\ndelete pair B A\n",
         {"RESIDUAL_REFERENCE"}, None),

        ("删除后引用也随之删除（无残留）",
         "entity A\nentity B\n"
         "ref A B\npair A B\npair B A\n"
         "delete pair A B\ndelete pair B A\ndelete ref A B\n",
         set(), 0),

        ("删除从未定义的关系",
         "entity A\nentity B\ndelete pair A B\n",
         {"DELETE_UNKNOWN"}, None),

        ("实体重复定义",
         "entity A\nentity A\n",
         {"DUPLICATE_ENTITY"}, None),

        ("语法错误",
         "entity\npair A\nbogus X Y\n",
         {"SYNTAX"}, None),
    ]

    all_ok = True
    for name, text, expect_codes, expect_count in cases:
        errors = check(text)
        codes = {e.code for e in errors}
        ok = expect_codes <= codes
        if expect_count is not None:
            ok = ok and len(errors) == expect_count
        status = "PASS" if ok else "FAIL"
        all_ok = all_ok and ok
        print("[%s] %s" % (status, name))
        print("       期望错误码: %s" % (sorted(expect_codes) or "（无）"))
        print("       实际错误码: %s" % (sorted(codes) or "（无）"))
        if not ok:
            for e in errors:
                print("         %s" % e)
    print()
    print("自测结果: %s（%d/%d 通过）"
          % ("全部通过" if all_ok else "存在失败",
             sum(1 for n, t, c, cnt in cases
                 if c <= {e.code for e in check(t)}
                 and (cnt is None or len(check(t)) == cnt)),
             len(cases)))
    return 0 if all_ok else 1


# ---------------------------------------------------------------- 入口

def main(argv):
    if len(argv) >= 2 and argv[1] == "--selftest":
        return selftest()
    if len(argv) >= 2 and argv[1] == "--demo":
        text = DEMO
        print("---- 演示输入 ----")
        print(text)
        print("---- 校验输出 ----")
    elif len(argv) >= 2:
        with open(argv[1], encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    errors = check(text)
    print(report(errors))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
