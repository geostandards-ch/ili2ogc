"""ili2db's dataset/basket and metadata tables (`T_ILI2DB_*`), as ili2pg/ili2gpkg 5.x define them.

Same table and column definitions, so a schema produced here is managed per
dataset and basket the way ili2db's own are. `meta_rows` fills the
mapping tables from the converted tables and models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from interlis.runtime.parse import read_ili_text

from .identifiers import OID_COLUMN, SEQUENCE
from .model import Table

# (name, PostgreSQL body, GeoPackage body) - ili2pg 5.5 / ili2gpkg definitions.
_META_TABLES = [
    (
        "T_ILI2DB_DATASET",
        "T_Id bigint PRIMARY KEY, datasetName varchar(200) NULL",
        "T_Id INTEGER PRIMARY KEY, datasetName TEXT(200) NULL",
    ),
    (
        "T_ILI2DB_BASKET",
        "T_Id bigint PRIMARY KEY, dataset bigint NULL, topic varchar(200) NOT NULL, T_Ili_Tid varchar(200) NULL, "
        "attachmentKey varchar(200) NOT NULL, domains varchar(1024) NULL",
        "T_Id INTEGER PRIMARY KEY, dataset INTEGER NULL REFERENCES T_ILI2DB_DATASET DEFERRABLE INITIALLY DEFERRED, "
        "topic TEXT(200) NOT NULL, T_Ili_Tid TEXT(200) NULL, attachmentKey TEXT(200) NOT NULL, "
        "domains TEXT(1024) NULL",
    ),
    (
        "T_ILI2DB_INHERITANCE",
        "thisClass varchar(1024) PRIMARY KEY, baseClass varchar(1024) NULL",
        "thisClass TEXT(1024) PRIMARY KEY, baseClass TEXT(1024) NULL",
    ),
    (
        "T_ILI2DB_SETTINGS",
        "tag varchar(60) PRIMARY KEY, setting varchar(8000) NULL",
        "tag TEXT(60) PRIMARY KEY, setting TEXT(8000) NULL",
    ),
    (
        "T_ILI2DB_TRAFO",
        "iliname varchar(1024) NOT NULL, tag varchar(1024) NOT NULL, setting varchar(1024) NOT NULL",
        "iliname TEXT(1024) NOT NULL, tag TEXT(1024) NOT NULL, setting TEXT(1024) NOT NULL",
    ),
    (
        "T_ILI2DB_MODEL",
        "filename varchar(250) NOT NULL, iliversion varchar(3) NOT NULL, modelName text NOT NULL, "
        "content text NOT NULL, importDate timestamp NOT NULL, PRIMARY KEY (modelName, iliversion)",
        "filename TEXT(250) NOT NULL, iliversion TEXT(3) NOT NULL, modelName TEXT NOT NULL, content TEXT NOT NULL, "
        "importDate DATETIME NOT NULL, PRIMARY KEY (modelName, iliversion)",
    ),
    (
        "T_ILI2DB_CLASSNAME",
        "IliName varchar(1024) PRIMARY KEY, SqlName varchar(1024) NOT NULL",
        "IliName TEXT(1024) PRIMARY KEY, SqlName TEXT(1024) NOT NULL",
    ),
    (
        "T_ILI2DB_ATTRNAME",
        "IliName varchar(1024) NOT NULL, SqlName varchar(1024) NOT NULL, ColOwner varchar(1024) NOT NULL, "
        "Target varchar(1024) NULL, PRIMARY KEY (SqlName, ColOwner)",
        "IliName TEXT(1024) NOT NULL, SqlName TEXT(1024) NOT NULL, ColOwner TEXT(1024) NOT NULL, "
        "Target TEXT(1024) NULL, PRIMARY KEY (SqlName, ColOwner)",
    ),
    (
        "T_ILI2DB_COLUMN_PROP",
        "tablename varchar(255) NOT NULL, subtype varchar(255) NULL, columnname varchar(255) NOT NULL, "
        "tag varchar(1024) NOT NULL, setting varchar(8000) NOT NULL",
        "tablename TEXT(255) NOT NULL, subtype TEXT(255) NULL, columnname TEXT(255) NOT NULL, "
        "tag TEXT(1024) NOT NULL, setting TEXT(8000) NOT NULL",
    ),
    (
        "T_ILI2DB_TABLE_PROP",
        "tablename varchar(255) NOT NULL, tag varchar(1024) NOT NULL, setting varchar(8000) NOT NULL",
        "tablename TEXT(255) NOT NULL, tag TEXT(1024) NOT NULL, setting TEXT(8000) NOT NULL",
    ),
    (
        "T_ILI2DB_META_ATTRS",
        "ilielement varchar(255) NOT NULL, attr_name varchar(1024) NOT NULL, attr_value varchar(8000) NOT NULL",
        "ilielement TEXT(255) NOT NULL, attr_name TEXT(1024) NOT NULL, attr_value TEXT(8000) NOT NULL",
    ),
]

# What this converter does, in ili2db's own setting vocabulary.
_SETTINGS = [
    ("ch.ehi.ili2db.BasketHandling", "readWrite"),
    ("ch.ehi.ili2db.TidHandling", "property"),
    ("ch.ehi.ili2db.inheritanceTrafo", "smart2"),
    ("ch.ehi.ili2db.catalogueRefTrafo", "coalesce"),
    ("ch.ehi.ili2db.multiSurfaceTrafo", "coalesce"),
    ("ch.ehi.ili2db.multiLineTrafo", "coalesce"),
    ("ch.ehi.ili2db.multiPointTrafo", "coalesce"),
    ("ch.ehi.ili2db.StrokeArcs", "enable"),
    ("ch.ehi.ili2db.createForeignKey", "yes"),
    ("ch.ehi.ili2db.createForeignKeyIndex", "yes"),
    ("ch.ehi.sqlgen.createGeomIndex", "True"),
    ("ch.ehi.ili2db.uniqueConstraints", "create"),
    ("ch.ehi.ili2db.numericCheckConstraints", "create"),
    ("ch.ehi.ili2db.maxSqlNameLength", "63"),
    ("ch.ehi.ili2db.defaultSrsAuthority", "EPSG"),
    ("ch.ehi.ili2db.createMetaInfo", "True"),
]


@dataclass
class IliModelFile:
    """One `.ili` file of the conversion, as a T_ILI2DB_MODEL row."""

    filename: str
    iliversion: str
    model_name: str
    """ili2db's form: each model of the file followed by `{ <imports>}` when it imports any."""
    content: str


@dataclass
class Ili2dbMeta:
    """What the T_ILI2DB_* rows are built from, beyond the tables themselves."""

    sender: str = "ili2ogc"
    models: list[IliModelFile] = field(default_factory=list)
    inheritance: list[tuple[str, str | None]] = field(default_factory=list)


def meta_table_ddl(*, gpkg: bool) -> list[str]:
    """The sequence (PostgreSQL) and every T_ILI2DB_* table."""
    statements = [] if gpkg else [f"CREATE SEQUENCE {SEQUENCE};"]
    for name, pg_body, gpkg_body in _META_TABLES:
        statements.append(f"CREATE TABLE {name} ({gpkg_body if gpkg else pg_body});")
    if not gpkg:
        statements.append(
            "ALTER TABLE T_ILI2DB_BASKET ADD CONSTRAINT T_ILI2DB_BASKET_dataset_fkey FOREIGN KEY (dataset) "
            "REFERENCES T_ILI2DB_DATASET DEFERRABLE INITIALLY DEFERRED;"
        )
    statements.append("CREATE INDEX T_ILI2DB_BASKET_dataset_idx ON T_ILI2DB_BASKET (dataset);")
    return statements


def _literal(value: str | None) -> str:
    return "NULL" if value is None else "'" + value.replace("'", "''") + "'"


def meta_rows(tables: list[Table], meta: Ili2dbMeta, *, gpkg: bool) -> list[str]:
    """INSERTs filling the mapping tables: classes, attributes, inheritance, models, settings."""
    rows: list[str] = []
    classes = {t.ili_name: t.name for t in tables if t.ili_name and not t.union_of}
    for ili_name, sql_name in sorted(classes.items()):
        rows.append(
            f"INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ({_literal(ili_name)}, {_literal(sql_name)});"
        )
    for table in tables:
        if table.union_of:
            continue
        targets = {fk.columns[0]: fk.ref_table for fk in table.foreign_keys if len(fk.columns) == 1}
        for column in table.columns:
            if column.ili_name:
                rows.append(
                    "INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES "
                    f"({_literal(column.ili_name)}, {_literal(column.name)}, {_literal(table.name)}, "
                    f"{_literal(targets.get(column.name))});"
                )
    for this_class, base_class in sorted(set(meta.inheritance)):
        rows.append(
            f"INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ({_literal(this_class)}, "
            f"{_literal(base_class)});"
        )
    now = "datetime('now')" if gpkg else "now()"
    for model in meta.models:
        rows.append(
            "INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES "
            f"({_literal(model.filename)}, {_literal(model.iliversion)}, {_literal(model.model_name)}, "
            f"{_literal(model.content)}, {now});"
        )
    for tag, setting in [("ch.ehi.ili2db.sender", meta.sender), *_SETTINGS]:
        rows.append(f"INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ({_literal(tag)}, {_literal(setting)});")
    return rows


def basket_column_ddl(table: Table, *, gpkg: bool) -> list[str]:
    """The technical columns opening every table: `t_id`, `t_basket`, and `t_ili_tid` when rows carry a TID."""
    if gpkg:
        lines = [f'    "{OID_COLUMN}" INTEGER PRIMARY KEY AUTOINCREMENT', '    "t_basket" INTEGER NOT NULL']
        tid = '    "t_ili_tid" TEXT(200)'
    else:
        lines = [
            f"    \"{OID_COLUMN}\" bigint PRIMARY KEY DEFAULT nextval('{SEQUENCE}')",
            '    "t_basket" bigint NOT NULL',
        ]
        tid = '    "t_ili_tid" varchar(200)'
    return [*lines, tid] if table.has_tid else lines


_ILI_VERSION_RE = re.compile(r"INTERLIS\s+(2\.\d)\s*;")
_MODEL_RE = re.compile(r"\b(?:TYPE\s+|REFSYSTEM\s+|SYMBOLOGY\s+)?MODEL\s+(\w+)")
_IMPORTS_RE = re.compile(r"\bIMPORTS\s+([^;]+);")


def model_file(path: Path) -> IliModelFile:
    """A `.ili` file as ili2db records it: each declared model with `{ <its imports>}`."""
    content = read_ili_text(path)
    code = re.sub(r"!!.*", "", re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL))
    version = _ILI_VERSION_RE.search(code)
    starts = [(m.start(), m.group(1)) for m in _MODEL_RE.finditer(code)]
    names = []
    for index, (start, name) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(code)
        imports = [
            imported
            for clause in _IMPORTS_RE.findall(code[start:end])
            for imported in re.split(r"[,\s]+", clause.strip())
            if imported and imported != "UNQUALIFIED"
        ]
        names.append(f"{name}{{ {' '.join(imports)}}}" if imports else name)
    return IliModelFile(path.name, version.group(1) if version else "2.3", " ".join(names), content)
