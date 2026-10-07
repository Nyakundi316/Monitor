"""Evaluate custom-PID formulas such as "(A*256+B)/4 - 40" without using eval().

Only numbers, the byte names A..Z and basic arithmetic/bitwise operators are allowed,
so a config file can never run arbitrary code.
"""
from __future__ import annotations

import ast
import operator

_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
    ast.LShift: operator.lshift, ast.RShift: operator.rshift,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


class FormulaError(ValueError):
    pass


def compile_formula(text: str) -> ast.Expression:
    if len(text) > 200:
        raise FormulaError("Formula too long")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Bad formula '{text}': {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load)):
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            continue
        if isinstance(node, ast.Name) and len(node.id) == 1 and "A" <= node.id <= "Z":
            continue
        if type(node) in _BIN or type(node) in _UNARY:
            continue
        raise FormulaError(f"'{type(node).__name__}' is not allowed in formulas")
    return tree


def evaluate(tree: ast.Expression, data: bytes) -> float:
    names = {chr(ord("A") + i): b for i, b in enumerate(data[:26])}

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise FormulaError(f"Byte {node.id} missing from the reply")
            return names[node.id]
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, (ast.LShift,)) and right > 64:
                raise FormulaError("Shift too large")
            return _BIN[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](ev(node.operand))
        raise FormulaError("Unsupported expression")

    return ev(tree)
