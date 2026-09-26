#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
relcheck.py — 实体/关系一致性校验工具（纯 Python 标准库，单文件）

用法:
    python3 relcheck.py 输入文件        # 校验指定文件
    python3 relcheck.py                 # 从标准输入读取
    python3 relcheck.py --selftest      # 运行内置自测样例

输入格式（每行一条指令，# 之后为注释，空行忽略）:
    entity <name>                  定义实体
    pair   <a> <b>                 配对关系（需对称闭环：存在 pair <b> <a>）
    ref    <a> <b>                 引用关系（目标实体必须已定义）
    refrel <a> <type> <x> <y>      实体 a 引用另一条关系（type ∈ pair/ref/map）
    map    <a> <b>                 映射关系（需双向唯一闭环：存在 map <b> <a>，
                                   且每个实体作为源/目标均唯一）
    delete <type> <a> <b>          删除关系（type ∈ pair/ref/map）

校验规则:
    1. 同类内部一致：配对成对、引用目标存在、映射双向唯一；
    2. 跨类交叉：同一实体同时出现在配对与映射中且约束冲突 -> 报告实体与两类关系；
    3. 关系内容引用不存在的实体 -> 报告；
    4. 关系重复定义 -> 报告；
    5. 跨类关系成环 -> 报告环上实体链；
    6. 关系删除后残留（引用已删关系）-> 报告；
    7. 跨行状态延续：输入结束时仍未闭环的配对/映射 -> 报告。
"""
import sys

REL_TYPES = ("pair", "ref", "map")

CAT_UNDEFINED_ENTITY = "未定义实体"
CAT_DUPLICATE = "重复定义"
CAT_UNCLOSED = "未闭环"
CAT_MAP_UNIQUE = "映射唯一性"
CAT_CROSS_CONFLICT = "跨类冲突"
CAT_CROSS_CYCLE = "跨类环"
CAT_RESIDUAL = "残留引用"
CAT_MISSING_REL = "引用不存在的关系"
CAT_DELETE_MISSING = "删除不存在的关系"
CAT_SYNTAX = "语法错误"


class Checker:
    def __init__(self):
        self.entities = {}   # name -> 定义行号
        self.relations = {}  # (type, a, b) -> 定义行号（当前生效）
        self.deleted = {}    # (type, a, b) -> (定义行号, 删除行号)
        self.errors = []     # (行号, 类别, 描述)，行号 0 表示全局汇总错误

    def _err(self, line_no, category, message):
        self.errors.append((line_no, category, message))

    def _check_entities(self, line_no, names):
        for name in names:
            if name not in self.entities:
                self._err(line_no, CAT_UNDEFINED_ENTITY, "实体 '%s' 未定义" % name)

    def feed(self, line_no, text):
        body = text.split("#", 1)[0].strip()
        if not body:
            return
        parts = body.split()
        cmd, args = parts[0], parts[1:]

        if cmd == "entity":
            if len(args) != 1:
                self._err(line_no, CAT_SYNTAX, "entity 指令需要 1 个参数: entity <name>")
                return
            name = args[0]
            if name in self.entities:
                self._err(line_no, CAT_DUPLICATE,
                          "实体 '%s' 重复定义（首次定义于第 %d 行）" % (name, self.entities[name]))
            else:
                self.entities[name] = line_no

        elif cmd in REL_TYPES:
            if len(args) != 2:
                self._err(line_no, CAT_SYNTAX, "%s 指令需要 2 个参数: %s <a> <b>" % (cmd, cmd))
                return
            a, b = args
            self._check_entities(line_no, (a, b))
            key = (cmd, a, b)
            if key in self.relations:
                self._err(line_no, CAT_DUPLICATE,
                          "关系 %s %s %s 重复定义（首次定义于第 %d 行）"
                          % (cmd, a, b, self.relations[key]))
            else:
                self.relations[key] = line_no
                self.deleted.pop(key, None)  # 删除后重新定义，视为复活

        elif cmd == "refrel":
            if len(args) != 4:
                self._err(line_no, CAT_SYNTAX, "refrel 指令需要 4 个参数: refrel <a> <type> <x> <y>")
                return
            a, rtype, x, y = args
            self._check_entities(line_no, (a,))
            if rtype not in REL_TYPES:
                self._err(line_no, CAT_SYNTAX, "未知关系类型 '%s'" % rtype)
                return
            key = (rtype, x, y)
            if key in self.deleted:
                del_line = self.deleted[key][1]
                self._err(line_no, CAT_RESIDUAL,
                          "实体 '%s' 引用了已删除的关系 %s %s %s（删除于第 %d 行），存在删除残留"
                          % (a, rtype, x, y, del_line))
            elif key not in self.relations:
                self._err(line_no, CAT_MISSING_REL,
                          "实体 '%s' 引用了不存在的关系 %s %s %s" % (a, rtype, x, y))

        elif cmd == "delete":
            if len(args) != 3:
                self._err(line_no, CAT_SYNTAX, "delete 指令需要 3 个参数: delete <type> <a> <b>")
                return
            rtype, a, b = args
            if rtype not in REL_TYPES:
                self._err(line_no, CAT_SYNTAX, "未知关系类型 '%s'" % rtype)
                return
            key = (rtype, a, b)
            if key in self.relations:
                self.deleted[key] = (self.relations.pop(key), line_no)
            else:
                self._err(line_no, CAT_DELETE_MISSING,
                          "删除不存在的关系 %s %s %s" % (rtype, a, b))
        else:
            self._err(line_no, CAT_SYNTAX, "无法识别的指令 '%s'" % cmd)

    # ---------------- 输入结束后的全局校验 ----------------

    def finalize(self):
        rels = self.relations
        by_line = sorted(rels.items(), key=lambda kv: kv[1])

        # 1) 配对成对闭环
        for (rtype, a, b), ln in by_line:
            if rtype == "pair" and ("pair", b, a) not in rels:
                self._err(ln, CAT_UNCLOSED,
                          "配对关系 pair %s %s 在输入结束时未闭环（缺少 pair %s %s）" % (a, b, b, a))

        # 2) 映射双向闭环 + 双向唯一
        map_src, map_tgt = {}, {}
        for (rtype, a, b), ln in by_line:
            if rtype != "map":
                continue
            map_src.setdefault(a, []).append((b, ln))
            map_tgt.setdefault(b, []).append((a, ln))
            if ("map", b, a) not in rels:
                self._err(ln, CAT_UNCLOSED,
                          "映射关系 map %s %s 在输入结束时未闭环（缺少 map %s %s）" % (a, b, b, a))
        for a, lst in sorted(map_src.items()):
            if len(lst) > 1:
                targets = ", ".join(sorted(b for b, _ in lst))
                self._err(lst[0][1], CAT_MAP_UNIQUE,
                          "实体 '%s' 同时映射到多个目标（%s），违反映射双向唯一" % (a, targets))
        for b, lst in sorted(map_tgt.items()):
            if len(lst) > 1:
                sources = ", ".join(sorted(a for a, _ in lst))
                self._err(lst[0][1], CAT_MAP_UNIQUE,
                          "实体 '%s' 被多个实体映射（%s），违反映射双向唯一" % (b, sources))

        # 3) 跨类交叉：同一实体同时出现在配对与映射中且约束冲突
        pair_partners, map_partners = {}, {}
        for (rtype, a, b), _ln in by_line:
            if rtype == "pair":
                pair_partners.setdefault(a, set()).add(b)
                pair_partners.setdefault(b, set()).add(a)  # 配对视为无向伙伴
            elif rtype == "map":
                map_partners.setdefault(a, set()).add(b)
        for e in sorted(set(pair_partners) & set(map_partners)):
            if pair_partners[e] != map_partners[e]:
                self._err(0, CAT_CROSS_CONFLICT,
                          "实体 '%s' 同时出现在配对与映射中且约束冲突："
                          "配对(pair)对象 %s，映射(map)对象 %s"
                          % (e, sorted(pair_partners[e]), sorted(map_partners[e])))

        # 4) 跨类关系环
        self._check_cycles()

    def _check_cycles(self):
        adj = {}
        for (rtype, a, b) in self.relations:
            adj.setdefault(a, []).append((b, rtype))
        for scc in self._tarjan_scc(adj):
            if len(scc) < 2:
                continue
            types = set()
            for node in scc:
                for (nb, rtype) in adj.get(node, []):
                    if nb in scc:
                        types.add(rtype)
            if len(types) >= 2:
                chain = self._find_cycle(next(iter(scc)), adj, scc)
                self._err(0, CAT_CROSS_CYCLE,
                          "检测到跨类关系环（涉及关系类型 %s），环上实体链：%s"
                          % (sorted(types), " -> ".join(chain)))

    @staticmethod
    def _tarjan_scc(adj):
        nodes = set(adj)
        for targets in adj.values():
            nodes.update(t for t, _ in targets)
        index_of, low, on_stack, stack, sccs = {}, {}, set(), [], []
        counter = [0]
        for root in sorted(nodes):
            if root in index_of:
                continue
            work = [(root, 0)]
            while work:
                node, ci = work[-1]
                if ci == 0:
                    index_of[node] = low[node] = counter[0]
                    counter[0] += 1
                    stack.append(node)
                    on_stack.add(node)
                neighbors = adj.get(node, [])
                recurse = False
                i = ci
                while i < len(neighbors):
                    nb = neighbors[i][0]
                    if nb not in index_of:
                        work[-1] = (node, i + 1)
                        work.append((nb, 0))
                        recurse = True
                        break
                    elif nb in on_stack:
                        low[node] = min(low[node], index_of[nb])
                    i += 1
                if recurse:
                    continue
                work.pop()
                if low[node] == index_of[node]:
                    scc = set()
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        scc.add(w)
                        if w == node:
                            break
                    sccs.append(scc)
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
        return sccs

    @staticmethod
    def _find_cycle(start, adj, scc):
        path, on_path = [start], {start}
        stack = [iter(adj.get(start, []))]
        while stack:
            advanced = False
            for (nb, _t) in stack[-1]:
                if nb not in scc:
                    continue
                if nb == start:
                    return path + [start]
                if nb not in on_path:
                    on_path.add(nb)
                    path.append(nb)
                    stack.append(iter(adj.get(nb, [])))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                on_path.discard(path.pop())
        return [start]


# ---------------- 驱动与输出 ----------------

def check_text(text):
    checker = Checker()
    for i, raw in enumerate(text.splitlines(), 1):
        checker.feed(i, raw)
    checker.finalize()
    return checker.errors


def format_report(errors):
    lines = []
    if not errors:
        lines.append("校验结果: 通过 (0 个错误)")
    else:
        lines.append("校验结果: 未通过 (%d 个错误)" % len(errors))
        lines.append("错误清单:")
        for i, (ln, cat, msg) in enumerate(sorted(errors, key=lambda e: (e[0], e[1])), 1):
            loc = "第%d行" % ln if ln else "全局"
            lines.append("  %2d. [%s] [%s] %s" % (i, loc, cat, msg))
    return "\n".join(lines)


# ---------------- 自测样例 ----------------

CLEAN_SAMPLE = """\
# 合法样例：全部校验通过
entity A
entity B
entity C
entity D
pair A B
pair B A
map C D
map D C
ref A C
ref B D
"""

BAD_SAMPLE = """\
# 异常样例：覆盖全部错误类别
entity A
entity B
entity C
entity D
entity E
entity F
entity G
entity H
entity I
entity J

# 跨类冲突 + 跨类环：A-B 配对、A->C 映射、B->C 引用
pair A B
pair B A
map A C
map C A
ref B C

# 未闭环配对（缺少 pair E D）
pair D E

# 映射双向唯一冲突（G 被 F、H 同时映射）+ 未闭环映射（缺少 map G H）
map F G
map G F
map H G

# 删除后残留引用
pair I J
pair J I
delete pair I J
delete pair J I
refrel A pair I J
refrel B map D E

# 其他错误
ref A Z
pair A B
delete ref A B
"""

EXPECTED_BAD_CATEGORIES = {
    CAT_UNDEFINED_ENTITY, CAT_DUPLICATE, CAT_UNCLOSED, CAT_MAP_UNIQUE,
    CAT_CROSS_CONFLICT, CAT_CROSS_CYCLE, CAT_RESIDUAL, CAT_MISSING_REL,
    CAT_DELETE_MISSING,
}


def run_selftest():
    ok = True

    print("== 自测 1：合法样例 ==")
    errors = check_text(CLEAN_SAMPLE)
    print(format_report(errors))
    if errors:
        ok = False
        print("[FAIL] 合法样例应 0 错误，实际 %d 个" % len(errors))
    else:
        print("[OK] 合法样例通过")

    print()
    print("== 自测 2：异常样例 ==")
    errors = check_text(BAD_SAMPLE)
    print(format_report(errors))
    got = {cat for _ln, cat, _msg in errors}
    missing = EXPECTED_BAD_CATEGORIES - got
    extra = got - EXPECTED_BAD_CATEGORIES
    if missing or extra:
        ok = False
        if missing:
            print("[FAIL] 缺少预期错误类别: %s" % sorted(missing))
        if extra:
            print("[FAIL] 出现意外错误类别: %s" % sorted(extra))
    else:
        print("[OK] 异常样例覆盖全部 %d 类错误" % len(EXPECTED_BAD_CATEGORIES))

    print()
    print("自测结果: %s" % ("全部通过" if ok else "存在失败"))
    return 0 if ok else 1


def main(argv):
    if "--selftest" in argv:
        return run_selftest()
    args = [a for a in argv[1:] if not a.startswith("-")]
    if args:
        with open(args[0], encoding="utf-8") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    errors = check_text(text)
    print(format_report(errors))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
