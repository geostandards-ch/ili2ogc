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
CREATE TABLE "gsnachfuehrung" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nbident" varchar(12) NOT NULL,
    "identifikator" varchar(12) NOT NULL,
    "gueltigereintrag" varchar(10) NOT NULL,
    "grundbucheintrag" varchar(10),
    CONSTRAINT uq_gsnachfuehrung_nbident_identifikator UNIQUE ("nbident", "identifikator")
);
CREATE TABLE "grundstueck" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nbident" varchar(12) NOT NULL,
    "nummer" varchar(12) NOT NULL,
    "egrid" varchar(14),
    "entstehung" bigint NOT NULL,
    "untergang" bigint
);
ALTER TABLE "gsnachfuehrung" ADD CONSTRAINT gsnachfuehrung_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck" ADD CONSTRAINT grundstueck_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck" ADD CONSTRAINT fk_grundstueck_entstehung FOREIGN KEY ("entstehung") REFERENCES "gsnachfuehrung" ("t_id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck" ADD CONSTRAINT fk_grundstueck_untergang FOREIGN KEY ("untergang") REFERENCES "gsnachfuehrung" ("t_id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_gsnachfuehrung_t_basket ON "gsnachfuehrung" ("t_basket");
CREATE INDEX idx_grundstueck_t_basket ON "grundstueck" ("t_basket");
CREATE INDEX idx_grundstueck_entstehung ON "grundstueck" ("entstehung");
CREATE INDEX idx_grundstueck_untergang ON "grundstueck" ("untergang");
CREATE VIEW "grundstueck_gueltig" AS
    SELECT
        "grundstueck"."t_id" AS "t_id",
        "grundstueck"."nbident" AS "nbident",
        "grundstueck"."nummer" AS "nummer",
        "grundstueck"."egrid" AS "egrid"
    FROM "grundstueck" "grundstueck"
    WHERE ("grundstueck"."entstehung" IS NOT NULL)
      AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = "grundstueck"."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL))
      AND ((NOT ("grundstueck"."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = "grundstueck"."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))));
CREATE OR REPLACE FUNCTION "uq_grundstueck_gueltig_ch041101_check"() RETURNS trigger AS $$
BEGIN
    IF ((NEW."nbident" IS NOT NULL AND NEW."nummer" IS NOT NULL) AND (NEW."entstehung" IS NOT NULL) AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = NEW."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL)) AND ((NOT (NEW."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = NEW."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))))) AND EXISTS (SELECT 1 FROM "grundstueck" "grundstueck" WHERE "grundstueck"."t_id" <> NEW."t_id" AND "grundstueck"."nbident" = NEW."nbident" AND "grundstueck"."nummer" = NEW."nummer" AND ("grundstueck"."entstehung" IS NOT NULL) AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = "grundstueck"."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL)) AND ((NOT ("grundstueck"."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = "grundstueck"."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))))) THEN
        RAISE EXCEPTION 'view "grundstueck_gueltig": UNIQUE CH041101 (nbident, nummer) violated';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER "uq_grundstueck_gueltig_ch041101_trg" BEFORE INSERT OR UPDATE ON "grundstueck"
    FOR EACH ROW EXECUTE FUNCTION "uq_grundstueck_gueltig_ch041101_check"();
CREATE OR REPLACE FUNCTION "uq_grundstueck_gueltig_ch041102_check"() RETURNS trigger AS $$
BEGIN
    IF ((NEW."egrid" IS NOT NULL) AND (NEW."entstehung" IS NOT NULL) AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = NEW."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL)) AND ((NOT (NEW."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = NEW."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))))) AND EXISTS (SELECT 1 FROM "grundstueck" "grundstueck" WHERE "grundstueck"."t_id" <> NEW."t_id" AND "grundstueck"."egrid" = NEW."egrid" AND ("grundstueck"."entstehung" IS NOT NULL) AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = "grundstueck"."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL)) AND ((NOT ("grundstueck"."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = "grundstueck"."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))))) THEN
        RAISE EXCEPTION 'view "grundstueck_gueltig": UNIQUE CH041102 (egrid) violated';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER "uq_grundstueck_gueltig_ch041102_trg" BEFORE INSERT OR UPDATE ON "grundstueck"
    FOR EACH ROW EXECUTE FUNCTION "uq_grundstueck_gueltig_ch041102_check"();
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.GSNachfuehrung', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Grundstueck', 'grundstueck');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.GSNachfuehrung.NBIdent', 'nbident', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.GSNachfuehrung.Identifikator', 'identifikator', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.GSNachfuehrung.GueltigerEintrag', 'gueltigereintrag', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.GSNachfuehrung.Grundbucheintrag', 'grundbucheintrag', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Grundstueck.NBIdent', 'nbident', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Grundstueck.Nummer', 'nummer', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Grundstueck.EGRID', 'egrid', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Entstehung_Grundstueck.Entstehung', 'entstehung', 'grundstueck', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Untergang_Grundstueck.Untergang', 'untergang', 'grundstueck', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Entstehung_Grundstueck', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.GSNachfuehrung', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Grundstueck', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_ViewPattern_V1.Grundstuecke.Untergang_Grundstueck', NULL);
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('dmav_view_pattern.ili', '2.4', 'DMAV_ViewPattern_V1', 'INTERLIS 2.4;

!! Minimal, self-contained reproduction of the DMAV (Datenmodell der
!! amtlichen Vermessung "Bund", swisstopo V+D) VIEW idiom - the shape a
!! 2026 survey of the published DMAV_* / SIA405_Abwasser_* model families
!! found to be the ONLY form real VIEW WHERE clauses use: PROJECTION OF one
!! class, WHERE = nested DEFINED() over association hops joined by
!! AND/OR/NOT, ending on a plain date attribute, plus a catalogue-numbered
!! view-level UNIQUE. No arithmetic, no function call.
!!
!! Modelled on V_D/DMAV_Grundstuecke_V1_1.ili''s `Grundstueck_Gueltig`
!! (https://models.geo.admin.ch/V_D/, VERSION 2026-01-31), reduced and
!! hermetic (no external IMPORTS). Like the real model, "Entstehung" and
!! "Untergang" are two roles of a single mutation-tracking class
!! (`GSNachfuehrung`) whose `Grundbucheintrag` is a nullable date - not a
!! separate class - so `DEFINED(Grundstueck->Entstehung->Grundbucheintrag)`
!! is an EXISTS over that class plus an `IS NOT NULL` on its date column.

MODEL DMAV_ViewPattern_V1 (de)
  AT "mailto:test@example.org" VERSION "2026-08-29" =

  DOMAIN
    NBIdent = TEXT*12;

  TOPIC Grundstuecke =

    CLASS GSNachfuehrung =
      NBIdent : MANDATORY NBIdent;
      Identifikator : MANDATORY TEXT*12;
      GueltigerEintrag : MANDATORY TEXT*10;
      Grundbucheintrag : TEXT*10;
      UNIQUE CH040101: NBIdent, Identifikator;
    END GSNachfuehrung;

    CLASS Grundstueck =
      NBIdent : MANDATORY NBIdent;
      Nummer : MANDATORY TEXT*12;
      EGRID : TEXT*14;
    END Grundstueck;

    ASSOCIATION Entstehung_Grundstueck =
      Entstehung -- {1} GSNachfuehrung;
      entstehendes_Grundstueck -- {0..*} Grundstueck;
    END Entstehung_Grundstueck;

    ASSOCIATION Untergang_Grundstueck =
      Untergang -- {0..1} GSNachfuehrung;
      untergehendes_Grundstueck -- {0..*} Grundstueck;
    END Untergang_Grundstueck;

    VIEW Grundstueck_Gueltig
      PROJECTION OF Grundstueck;
      WHERE DEFINED(Grundstueck->Entstehung)
        AND DEFINED(Grundstueck->Entstehung->Grundbucheintrag)
        AND (NOT(DEFINED(Grundstueck->Untergang))
             OR NOT(DEFINED(Grundstueck->Untergang->Grundbucheintrag)));
      =
      ALL OF Grundstueck;
      UNIQUE CH041101: NBIdent, Nummer;
      UNIQUE CH041102: EGRID;
    END Grundstueck_Gueltig;

  END Grundstuecke;

END DMAV_ViewPattern_V1.
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
