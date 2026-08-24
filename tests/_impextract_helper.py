"""Extract COMPLETE import statements covering given names, via AST."""
import ast


def complete_imports_for(path: str, names: set[str]) -> str:
    src = open(path).read()
    tree = ast.parse(src)
    out = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if (a.asname or a.name).split('.')[0] in names:
                    out.append(ast.unparse(node))
        elif isinstance(node, ast.ImportFrom):
            imported = {(a.asname or a.name) for a in node.names}
            if imported & names:
                out.append(ast.unparse(node))
    return '\n'.join(out) + '\n'
