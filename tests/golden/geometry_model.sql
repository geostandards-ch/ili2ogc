CREATE SEQUENCE t_ili2db_seq;
CREATE TABLE T_ILI2DB_DATASET (T_Id bigint PRIMARY KEY, datasetName varchar(200) NULL);
CREATE TABLE T_ILI2DB_BASKET (T_Id bigint PRIMARY KEY, dataset bigint NULL, topic varchar(200) NOT NULL, T_Ili_Tid varchar(200) NULL, attachmentKey varchar(200) NOT NULL, domains varchar(1024) NULL);
CREATE TABLE T_ILI2DB_INHERITANCE (thisClass varchar(1024) PRIMARY KEY, baseClass varchar(1024) NULL);
CREATE TABLE T_ILI2DB_SETTINGS (tag varchar(60) PRIMARY KEY, setting varchar(8000) NULL);
CREATE TABLE T_ILI2DB_TRAFO (iliname varchar(1024) NOT NULL, tag varchar(1024) NOT NULL, setting varchar(1024) NOT NULL);
CREATE TABLE T_ILI2DB_MODEL (filename varchar(250) NOT NULL, iliversion varchar(3) NOT NULL, modelName text NOT NULL, content text NOT NULL, importDate timestamp NOT NULL, PRIMARY KEY (modelName, iliversion));
CREATE TABLE T_ILI2DB_CLASSNAME (IliName varchar(1024) PRIMARY KEY, SqlName varchar(1024) NOT NULL);
CREATE TABLE T_ILI2DB_ATTRNAME (IliName varchar(1024) NOT NULL, SqlName varchar(1024) NOT NULL, ColOwner varchar(1024) NOT NULL, Target varchar(1024) NULL, PRIMARY KEY (SqlName, ColOwner));
CREATE TABLE T_ILI2DB_COLUMN_PROP (tablename varchar(255) NOT NULL, subtype varchar(255) NULL, columnname varchar(255) NOT NULL, tag varchar(1024) NOT NULL, setting varchar(8000) NOT NULL);
CREATE TABLE T_ILI2DB_TABLE_PROP (tablename varchar(255) NOT NULL, tag varchar(1024) NOT NULL, setting varchar(8000) NOT NULL);
CREATE TABLE T_ILI2DB_META_ATTRS (ilielement varchar(255) NOT NULL, attr_name varchar(1024) NOT NULL, attr_value varchar(8000) NOT NULL);
ALTER TABLE T_ILI2DB_BASKET ADD CONSTRAINT T_ILI2DB_BASKET_dataset_fkey FOREIGN KEY (dataset) REFERENCES T_ILI2DB_DATASET DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX T_ILI2DB_BASKET_dataset_idx ON T_ILI2DB_BASKET (dataset);
CREATE TABLE "point" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200)
);
-- NOTE (point): [SQL-GEOM-NO-CRS] Pos: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "multipoint" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200)
);
-- NOTE (multipoint): [SQL-GEOM-NO-CRS] Pos: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "way" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200)
);
-- NOTE (way): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "directedway" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200)
);
-- NOTE (directedway): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "multiway" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200)
);
-- NOTE (multiway): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
CREATE TABLE "zone" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200)
);
-- NOTE (zone): [SQL-GEOM-NO-CRS] Geom: no resolved CRS (!!@CRS meta-attribute) - provide the geometry base model via --repo
ALTER TABLE "point" ADD CONSTRAINT point_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "multipoint" ADD CONSTRAINT multipoint_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "way" ADD CONSTRAINT way_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "directedway" ADD CONSTRAINT directedway_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "multiway" ADD CONSTRAINT multiway_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "zone" ADD CONSTRAINT zone_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_point_t_basket ON "point" ("t_basket");
CREATE INDEX idx_multipoint_t_basket ON "multipoint" ("t_basket");
CREATE INDEX idx_way_t_basket ON "way" ("t_basket");
CREATE INDEX idx_directedway_t_basket ON "directedway" ("t_basket");
CREATE INDEX idx_multiway_t_basket ON "multiway" ("t_basket");
CREATE INDEX idx_zone_t_basket ON "zone" ("t_basket");
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('GeomTest.MainTopic.DirectedWay', 'directedway');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('GeomTest.MainTopic.MultiPoint', 'multipoint');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('GeomTest.MainTopic.MultiWay', 'multiway');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('GeomTest.MainTopic.Point', 'point');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('GeomTest.MainTopic.Way', 'way');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('GeomTest.MainTopic.Zone', 'zone');
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('GeomTest.MainTopic.DirectedWay', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('GeomTest.MainTopic.MultiPoint', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('GeomTest.MainTopic.MultiWay', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('GeomTest.MainTopic.Point', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('GeomTest.MainTopic.Way', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('GeomTest.MainTopic.Zone', NULL);
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('geometry_model.ili', '2.4', 'GeomTest', 'INTERLIS 2.4;

MODEL GeomTest AT "https://example.org/geomtest" VERSION "2026-08-07" =

  TOPIC MainTopic =

    DOMAIN
      Coord2 = COORD
        0.000 .. 100.000,
        0.000 .. 200.000;

      MultiCoord2 = MULTICOORD
        0.000 .. 100.000,
        0.000 .. 200.000;

      Line = POLYLINE WITH (STRAIGHTS,ARCS) VERTEX Coord2;
      DirectedLine EXTENDS Line = DIRECTED POLYLINE;
      MultiLine = MULTIPOLYLINE WITH (STRAIGHTS,ARCS) VERTEX Coord2;
      Area = SURFACE WITH (STRAIGHTS,ARCS) VERTEX Coord2 WITHOUT OVERLAPS > 0.001;

    CLASS Point =
      Pos: Coord2;
    END Point;

    CLASS MultiPoint =
      Pos: MultiCoord2;
    END MultiPoint;

    CLASS Way =
      Geom: Line;
    END Way;

    CLASS DirectedWay =
      Geom: DirectedLine;
    END DirectedWay;

    CLASS MultiWay =
      Geom: MultiLine;
    END MultiWay;

    CLASS Zone =
      Geom: Area;
    END Zone;

  END MainTopic;

END GeomTest.
', now());
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.sender', 'ili2ogc');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.BasketHandling', 'readWrite');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.TidHandling', 'property');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.inheritanceTrafo', 'smart2');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.catalogueRefTrafo', 'coalesce');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.multiSurfaceTrafo', 'coalesce');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.multiLineTrafo', 'coalesce');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.multiPointTrafo', 'coalesce');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.StrokeArcs', 'enable');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.createForeignKey', 'yes');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.createForeignKeyIndex', 'yes');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.sqlgen.createGeomIndex', 'True');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.uniqueConstraints', 'create');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.numericCheckConstraints', 'create');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.maxSqlNameLength', '63');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.defaultSrsAuthority', 'EPSG');
INSERT INTO T_ILI2DB_SETTINGS (tag, setting) VALUES ('ch.ehi.ili2db.createMetaInfo', 'True');
