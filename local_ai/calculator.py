"""Small, exact decimal calculator; never execute model-supplied code."""
import ast
from decimal import Decimal, DecimalException, localcontext
import operator


def calculate(expression):
    if not isinstance(expression,str) or not 1 <= len(expression) <= 500:
        raise ValueError('算式须为 1–500 字符。')
    try:
        tree = ast.parse(expression,mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 100:
            raise ValueError('算式过于复杂。')
        binary = {ast.Add:operator.add, ast.Sub:operator.sub, ast.Mult:operator.mul, ast.Div:operator.truediv}
        comparisons = {ast.Lt:operator.lt,ast.LtE:operator.le,ast.Gt:operator.gt,ast.GtE:operator.ge,ast.Eq:operator.eq,ast.NotEq:operator.ne}
        def visit(node):
            if isinstance(node,ast.Constant) and type(node.value) in (int,float):
                value = Decimal(ast.get_source_segment(expression,node))
                if not value.is_finite() or abs(value) > Decimal('1e100'):
                    raise ValueError('数字超出范围。')
                return value
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
                value = visit(node.operand)
                if isinstance(value,bool): raise ValueError('不能对布尔结果进行算术。')
                return value if isinstance(node.op,ast.UAdd) else -value
            if isinstance(node,ast.BinOp) and type(node.op) in binary:
                left,right = visit(node.left),visit(node.right)
                if isinstance(left,bool) or isinstance(right,bool): raise ValueError('不能对布尔结果进行算术。')
                return binary[type(node.op)](left,right)
            if isinstance(node,ast.Compare):
                values = [visit(node.left),*(visit(n) for n in node.comparators)]
                if any(isinstance(v,bool) for v in values) or any(type(op) not in comparisons for op in node.ops):
                    raise ValueError('不支持该比较。')
                return all(comparisons[type(op)](a,b) for op,a,b in zip(node.ops,values,values[1:]))
            if isinstance(node,ast.BoolOp) and isinstance(node.op,(ast.And,ast.Or)):
                values = [visit(v) for v in node.values]
                if not all(isinstance(v,bool) for v in values): raise ValueError('and/or 两侧必须是比较条件。')
                return all(values) if isinstance(node.op,ast.And) else any(values)
            raise ValueError('只支持数字、四则运算、比较与 and/or；不支持代码、变量或函数。')
        with localcontext() as context:
            context.prec = 50
            value = visit(tree.body)
        return {'expression':expression,'value':value if isinstance(value,bool) else str(value),
                'note':'数字来自调用参数；计算结果不能证明来源数据真实。'}
    except (SyntaxError,DecimalException,RecursionError) as exc:
        raise ValueError('算式无效、除以零或数字超出范围。') from exc
