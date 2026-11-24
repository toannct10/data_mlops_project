"""Shared SELECT-only AST policy; privileges and timeouts remain separate gates."""
from src.monitoring.ai_timing import stage
import sqlglot
from sqlglot import exp


def parse_select(statement, dialect='postgres'):
    if not isinstance(statement,str) or len(statement)>6000:
        raise ValueError('sql_size')
    if any(x in statement for x in ('--','/*','*/')):
        raise ValueError('sql_comments')
    text=statement.strip().removesuffix(';').strip()
    if ';' in text:raise ValueError('multiple_statements')
    try:trees=sqlglot.parse(text,read=dialect,error_level='RAISE')
    except sqlglot.errors.SqlglotError as error:raise ValueError('sql_syntax') from error
    if len(trees)!=1 or not isinstance(trees[0],exp.Select):raise ValueError('non_select')
    tree=trees[0]
    forbidden=(exp.Insert,exp.Update,exp.Delete,exp.Drop,exp.Create,exp.Alter,
               exp.Command,exp.Copy,exp.Merge,exp.Into,exp.Transaction,
               exp.Grant,exp.Revoke,exp.Lock,exp.Union,exp.Intersect,exp.Except)
    if any(isinstance(n,forbidden) for n in tree.walk()):raise ValueError('unsafe_operation')
    return tree


@stage('sql_validation')
def validate_analytical_sql(statement,tables,*,dialect='postgres',max_rows=100):
    """CTEs can only consume approved base tables and approved pure functions."""
    tree=parse_select(statement,dialect)
    ctes={c.alias.lower() for c in tree.find_all(exp.CTE)}
    aliases={a.alias.lower() for a in tree.find_all(exp.Alias)}
    used={};schema={k.lower():{c.lower() for c in v} for k,v in tables.items()}
    for t in tree.find_all(exp.Table):
        key='.'.join(x.name.lower() for x in t.parts)
        if key in ctes:continue
        if key not in schema:raise ValueError('table_allowlist')
        used[t.alias_or_name.lower()]=key
    if not used:raise ValueError('table_allowlist')
    all_columns=set().union(*(schema[t] for t in used.values()))
    for c in tree.find_all(exp.Column):
        if c.name.lower() not in all_columns|aliases:raise ValueError('column_allowlist')
        if c.table.lower() in used and c.name.lower() not in schema[used[c.table.lower()]]:
            raise ValueError('column_allowlist')
    if any(not isinstance(s.parent,exp.Count) for s in tree.find_all(exp.Star)):
        raise ValueError('wildcard_select')
    safe={'SUM','COUNT','AVG','MIN','MAX','ROUND','COALESCE','NULLIF','DATE_TRUNC',
          'TIMESTAMP_TRUNC','DATE','UPPER','LOWER','ABS','EXTRACT','GREATEST','LEAST','CASE','IF','COUNT_IF'}
    for f in tree.find_all(exp.Func):
        if isinstance(f,(exp.Binary,exp.Unary,exp.Predicate)):continue
        if isinstance(f,exp.Cast):
            if f.args['to'].this not in {exp.DataType.Type.DATE,exp.DataType.Type.DECIMAL,
                                       exp.DataType.Type.DOUBLE,exp.DataType.Type.TEXT,
                                       exp.DataType.Type.TIMESTAMP}:raise ValueError('cast_type')
        elif f.sql_name().upper() not in safe:raise ValueError('function_allowlist')
    if any(j.args.get('kind')=='CROSS' or j.args.get('on') is None for j in tree.find_all(exp.Join)):
        raise ValueError('unsafe_join')
    limit=tree.args.get('limit')
    if limit is not None:
        try:number=int(limit.expression.this)
        except (TypeError,ValueError,AttributeError):raise ValueError('row_limit') from None
        if not 1<=number<=max_rows:raise ValueError('row_limit')
    else:tree=tree.limit(max_rows)
    return tree.sql(dialect=dialect),sorted(set(used.values()))
