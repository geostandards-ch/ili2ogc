CREATE TABLE "point" (
    "id" text PRIMARY KEY
);
-- NOTE (point): [SQL-GEOM-NO-CRS] Pos: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "multipoint" (
    "id" text PRIMARY KEY
);
-- NOTE (multipoint): [SQL-GEOM-NO-CRS] Pos: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "way" (
    "id" text PRIMARY KEY
);
-- NOTE (way): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "directedway" (
    "id" text PRIMARY KEY
);
-- NOTE (directedway): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "multiway" (
    "id" text PRIMARY KEY
);
-- NOTE (multiway): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "zone" (
    "id" text PRIMARY KEY
);
-- NOTE (zone): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
