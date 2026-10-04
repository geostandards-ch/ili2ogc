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
CREATE TABLE "wegabschnitt" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "bezeichnung" varchar(40) NOT NULL,
    "kategorie" text NOT NULL,
    "belagsart" varchar(20),
    CONSTRAINT chk_wegabschnitt_kategorie_domain CHECK ("kategorie" IN ('AlpinWanderweg', 'Bergwanderweg', 'Wanderweg'))
);
CREATE TABLE "wegweiser" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "standort" varchar(40) NOT NULL,
    "hoehe" integer,
    "wegabschnitt" bigint NOT NULL,
    CONSTRAINT chk_wegweiser_hoehe_domain CHECK ("hoehe" BETWEEN 0 AND 5000)
);
ALTER TABLE "wegabschnitt" ADD CONSTRAINT wegabschnitt_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "wegweiser" ADD CONSTRAINT wegweiser_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "wegweiser" ADD CONSTRAINT fk_wegweiser_wegabschnitt FOREIGN KEY ("wegabschnitt") REFERENCES "wegabschnitt" ("t_id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_wegabschnitt_t_basket ON "wegabschnitt" ("t_basket");
CREATE INDEX idx_wegweiser_t_basket ON "wegweiser" ("t_basket");
CREATE INDEX idx_wegweiser_wegabschnitt ON "wegweiser" ("wegabschnitt");
CREATE VIEW "wegabschnitt_mitwegweiser" AS
    SELECT
        "wegabschnitt"."t_id" AS "t_id",
        "wegabschnitt"."bezeichnung" AS "bezeichnung",
        "wegabschnitt"."kategorie" AS "kategorie",
        "wegabschnitt"."belagsart" AS "belagsart"
    FROM "wegabschnitt" "wegabschnitt"
    WHERE (EXISTS (SELECT 1 FROM "wegweiser" "v1" WHERE "v1"."wegabschnitt" = "wegabschnitt"."t_id"));
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('Wanderwege_V1.Netz.Wegabschnitt', 'wegabschnitt');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('Wanderwege_V1.Netz.Wegweiser', 'wegweiser');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('Wanderwege_V1.Netz.Wegabschnitt.Bezeichnung', 'bezeichnung', 'wegabschnitt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('Wanderwege_V1.Netz.Wegabschnitt.Kategorie', 'kategorie', 'wegabschnitt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('Wanderwege_V1.Netz.Wegabschnitt.Belagsart', 'belagsart', 'wegabschnitt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('Wanderwege_V1.Netz.Wegweiser.Standort', 'standort', 'wegweiser', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('Wanderwege_V1.Netz.Wegweiser.Hoehe', 'hoehe', 'wegweiser', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('Wanderwege_V1.Netz.Abschnitt_Wegweiser.Wegabschnitt', 'wegabschnitt', 'wegweiser', 'wegabschnitt');
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('Wanderwege_V1.Netz.Abschnitt_Wegweiser', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('Wanderwege_V1.Netz.Wegabschnitt', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('Wanderwege_V1.Netz.Wegweiser', NULL);
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('Wanderwege_V1.ili', '2.4', 'Wanderwege_V1', 'INTERLIS 2.4;

!! Minimal base model for the TRANSLATION OF / `--lang` overlay test -
!! two classes, an association (embedded FK), and a PROJECTION view.

MODEL Wanderwege_V1 (de)
  AT "mailto:test@example.org" VERSION "2026-08-29" =

  DOMAIN
    Wegkategorie = (Wanderweg, Bergwanderweg, AlpinWanderweg);

  TOPIC Netz =

    CLASS Wegabschnitt =
      Bezeichnung : MANDATORY TEXT*40;
      Kategorie : MANDATORY Wegkategorie;
      Belagsart : TEXT*20;
    END Wegabschnitt;

    CLASS Wegweiser =
      Standort : MANDATORY TEXT*40;
      Hoehe : 0 .. 5000;
    END Wegweiser;

    ASSOCIATION Abschnitt_Wegweiser =
      Wegabschnitt -- {1} Wegabschnitt;
      wegweiser_von -- {0..*} Wegweiser;
    END Abschnitt_Wegweiser;

    VIEW Wegabschnitt_MitWegweiser
      PROJECTION OF Wegabschnitt;
      WHERE DEFINED(Wegabschnitt->wegweiser_von);
      =
      ALL OF Wegabschnitt;
    END Wegabschnitt_MitWegweiser;

  END Netz;

END Wanderwege_V1.
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
