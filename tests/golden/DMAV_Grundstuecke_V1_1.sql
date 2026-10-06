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
CREATE TABLE "liegenschaft" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nummerteilgrundstueck" varchar(12),
    "fiktiv" boolean NOT NULL,
    "flaechenmass" integer NOT NULL,
    "qualitaetsstandard" text NOT NULL,
    "grundstueck" bigint NOT NULL,
    CONSTRAINT chk_liegenschaft_flaechenmass_domain CHECK ("flaechenmass" BETWEEN 1 AND 999999999),
    CONSTRAINT chk_liegenschaft_qualitaetsstandard_domain CHECK ("qualitaetsstandard" IN ('AV93', 'PN'))
);
-- NOTE (liegenschaft): [SQL-GEOM-NO-CRS] Geometrie: vertex CoordType not resolved - provide the geometry base model via --repo
-- NOTE (liegenschaft): [BUILD-TYPE-UNRESOLVED] Streitig: attribute type not resolved by the model builder - provide the imported model via --repo
-- NOTE (liegenschaft): [SQL-CHECK-EXPR-UNSUPPORTED] MANDATORY CONSTRAINT 'CH041201': DEFINED(streitig): no such column - CHECK not generated
CREATE TABLE "selbstaendigesdauerndesrecht" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nummerteilgrundstueck" varchar(12),
    "flaechenmass" integer NOT NULL,
    "istbaurecht" boolean,
    "grundstueck" bigint NOT NULL,
    CONSTRAINT chk_selbstaendigesdauerndesrecht_flaechenmass_domain CHECK ("flaechenmass" BETWEEN 1 AND 999999999)
);
-- NOTE (selbstaendigesdauerndesrecht): [SQL-GEOM-NO-CRS] Geometrie: vertex CoordType not resolved - provide the geometry base model via --repo
-- NOTE (selbstaendigesdauerndesrecht): [BUILD-TYPE-UNRESOLVED] Streitig: attribute type not resolved by the model builder - provide the imported model via --repo
-- NOTE (selbstaendigesdauerndesrecht): [SQL-CHECK-EXPR-UNSUPPORTED] MANDATORY CONSTRAINT 'CH041601': DEFINED(streitig): no such column - CHECK not generated
CREATE TABLE "bergwerk" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nummerteilgrundstueck" varchar(12),
    "flaechenmass" integer NOT NULL,
    "grundstueck" bigint NOT NULL,
    CONSTRAINT chk_bergwerk_flaechenmass_domain CHECK ("flaechenmass" BETWEEN 1 AND 999999999)
);
-- NOTE (bergwerk): [SQL-GEOM-NO-CRS] Geometrie: vertex CoordType not resolved - provide the geometry base model via --repo
-- NOTE (bergwerk): [BUILD-TYPE-UNRESOLVED] Streitig: attribute type not resolved by the model builder - provide the imported model via --repo
-- NOTE (bergwerk): [SQL-CHECK-EXPR-UNSUPPORTED] MANDATORY CONSTRAINT 'CH042001': DEFINED(streitig): no such column - CHECK not generated
CREATE TABLE "gsnachfuehrung" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nbident" varchar(12) NOT NULL,
    "identifikator" varchar(12) NOT NULL,
    "beschreibung" varchar(60) NOT NULL,
    "mutationsart" text NOT NULL,
    "gueltigereintrag" timestamp NOT NULL,
    "grundbucheintrag" timestamp,
    CONSTRAINT uq_gsnachfuehrung_nbident_identifikator UNIQUE ("nbident", "identifikator"),
    CONSTRAINT chk_gsnachfuehrung_mutationsart_domain CHECK ("mutationsart" IN ('AbschlussProjektmutation', 'Normal', 'Projektmutation'))
);
-- NOTE (gsnachfuehrung): [SQL-GEOM-NO-CRS] Perimeter: vertex CoordType not resolved - provide the geometry base model via --repo
CREATE TABLE "grenzpunkt" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nbident" varchar(12),
    "nummer" varchar(12),
    "hoehengeometrie" numeric(7,3),
    "lagegenauigkeit" numeric(4,3) NOT NULL,
    "istlagezuverlaessig" boolean NOT NULL,
    "hoehengenauigkeit" numeric(4,3),
    "isthoehenzuverlaessig" boolean,
    "punktzeichen" text NOT NULL,
    "isthoheitsgrenzpunkt" boolean NOT NULL,
    "isthoheitsgrenzsteinalt" boolean NOT NULL,
    "istexaktdefiniert" boolean NOT NULL,
    "symbolori" numeric(4,1),
    "entstehung" bigint NOT NULL,
    "untergang" bigint,
    CONSTRAINT chk_grenzpunkt_hoehengeometrie_domain CHECK ("hoehengeometrie" BETWEEN -200.000 AND 5000.000),
    CONSTRAINT chk_grenzpunkt_lagegenauigkeit_domain CHECK ("lagegenauigkeit" BETWEEN 0.001 AND 7.000),
    CONSTRAINT chk_grenzpunkt_hoehengenauigkeit_domain CHECK ("hoehengenauigkeit" BETWEEN 0.001 AND 7.000),
    CONSTRAINT chk_grenzpunkt_punktzeichen_domain CHECK ("punktzeichen" IN ('Bolzen', 'Kreuz', 'Kunststoffzeichen', 'Pfahl', 'Rohr', 'Stein', 'unversichert', 'weitere')),
    CONSTRAINT chk_grenzpunkt_symbolori_domain CHECK ("symbolori" BETWEEN 0.0 AND 399.9),
    CONSTRAINT chk_grenzpunkt_ch040201 CHECK ((("hoehengeometrie" IS NOT NULL) = ("hoehengenauigkeit" IS NOT NULL))),
    CONSTRAINT chk_grenzpunkt_ch040202 CHECK ((("hoehengeometrie" IS NOT NULL) = ("isthoehenzuverlaessig" IS NOT NULL))),
    CONSTRAINT chk_grenzpunkt_ch040203 CHECK (("istexaktdefiniert" OR ("punktzeichen" = 'unversichert')))
);
-- NOTE (grenzpunkt): [BUILD-TYPE-UNRESOLVED] Geometrie: attribute type not resolved by the model builder - provide the imported model via --repo
CREATE TABLE "grundstueck" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "nbident" varchar(12) NOT NULL,
    "nummer" varchar(12) NOT NULL,
    "egrid" varchar(14),
    "iststreitig" boolean NOT NULL,
    "istvollstaendig" boolean NOT NULL,
    "grundstuecksart" text NOT NULL,
    "fiktiv" boolean NOT NULL,
    "gesamtflaechenmass" integer,
    "entstehung" bigint NOT NULL,
    "untergang" bigint,
    CONSTRAINT chk_grundstueck_grundstuecksart_domain CHECK ("grundstuecksart" IN ('Bergwerk', 'Liegenschaft', 'SelbstaendigesDauerndesRecht')),
    CONSTRAINT chk_grundstueck_gesamtflaechenmass_domain CHECK ("gesamtflaechenmass" BETWEEN 1 AND 999999999),
    CONSTRAINT chk_grundstueck_ch040701 CHECK (("istvollstaendig" = (NOT ("gesamtflaechenmass" IS NOT NULL))))
);
CREATE TABLE "grundstueck_textposition" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "grundstueck_fk" bigint NOT NULL,
    "orientierung" numeric(4,1),
    "darstellungin" text,
    "textgroesse" text,
    "hreferenzpunkt" text,
    "vreferenzpunkt" text,
    CONSTRAINT chk_grundstueck_textposition_orientierung_domain CHECK ("orientierung" BETWEEN 0.0 AND 399.9),
    CONSTRAINT chk_grundstueck_textposition_darstellungin_domain CHECK ("darstellungin" IN ('Basisplan', 'PlanFuerDasGrundbuch')),
    CONSTRAINT chk_grundstueck_textposition_textgroesse_domain CHECK ("textgroesse" IN ('Gross', 'Klein', 'Mittel')),
    CONSTRAINT chk_grundstueck_textposition_hreferenzpunkt_domain CHECK ("hreferenzpunkt" IN ('Center', 'Left', 'Right')),
    CONSTRAINT chk_grundstueck_textposition_vreferenzpunkt_domain CHECK ("vreferenzpunkt" IN ('Base', 'Bottom', 'Cap', 'Half', 'Top'))
);
-- NOTE (grundstueck_textposition): [BUILD-TYPE-UNRESOLVED] Position: attribute type not resolved by the model builder - provide the imported model via --repo
-- NOTE (grundstueck_textposition): [BUILD-TYPE-UNRESOLVED] Hinweisstrich: attribute type not resolved by the model builder - provide the imported model via --repo
ALTER TABLE "liegenschaft" ADD CONSTRAINT liegenschaft_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "liegenschaft" ADD CONSTRAINT fk_liegenschaft_grundstueck FOREIGN KEY ("grundstueck") REFERENCES "grundstueck" ("t_id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "selbstaendigesdauerndesrecht" ADD CONSTRAINT selbstaendigesdauerndesrecht_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "selbstaendigesdauerndesrecht" ADD CONSTRAINT fk_selbstaendigesdauerndesrecht_grundstueck FOREIGN KEY ("grundstueck") REFERENCES "grundstueck" ("t_id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "bergwerk" ADD CONSTRAINT bergwerk_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "bergwerk" ADD CONSTRAINT fk_bergwerk_grundstueck FOREIGN KEY ("grundstueck") REFERENCES "grundstueck" ("t_id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "gsnachfuehrung" ADD CONSTRAINT gsnachfuehrung_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grenzpunkt" ADD CONSTRAINT grenzpunkt_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grenzpunkt" ADD CONSTRAINT fk_grenzpunkt_entstehung FOREIGN KEY ("entstehung") REFERENCES "gsnachfuehrung" ("t_id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grenzpunkt" ADD CONSTRAINT fk_grenzpunkt_untergang FOREIGN KEY ("untergang") REFERENCES "gsnachfuehrung" ("t_id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck" ADD CONSTRAINT grundstueck_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck" ADD CONSTRAINT fk_grundstueck_entstehung FOREIGN KEY ("entstehung") REFERENCES "gsnachfuehrung" ("t_id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck" ADD CONSTRAINT fk_grundstueck_untergang FOREIGN KEY ("untergang") REFERENCES "gsnachfuehrung" ("t_id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck_textposition" ADD CONSTRAINT grundstueck_textposition_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "grundstueck_textposition" ADD CONSTRAINT fk_grundstueck_textposition_grundstueck_fk FOREIGN KEY ("grundstueck_fk") REFERENCES "grundstueck" ("t_id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_liegenschaft_t_basket ON "liegenschaft" ("t_basket");
CREATE INDEX idx_liegenschaft_grundstueck ON "liegenschaft" ("grundstueck");
CREATE INDEX idx_selbstaendigesdauerndesrecht_t_basket ON "selbstaendigesdauerndesrecht" ("t_basket");
CREATE INDEX idx_selbstaendigesdauerndesrecht_grundstueck ON "selbstaendigesdauerndesrecht" ("grundstueck");
CREATE INDEX idx_bergwerk_t_basket ON "bergwerk" ("t_basket");
CREATE INDEX idx_bergwerk_grundstueck ON "bergwerk" ("grundstueck");
CREATE INDEX idx_gsnachfuehrung_t_basket ON "gsnachfuehrung" ("t_basket");
CREATE INDEX idx_grenzpunkt_t_basket ON "grenzpunkt" ("t_basket");
CREATE INDEX idx_grenzpunkt_entstehung ON "grenzpunkt" ("entstehung");
CREATE INDEX idx_grenzpunkt_untergang ON "grenzpunkt" ("untergang");
CREATE INDEX idx_grundstueck_t_basket ON "grundstueck" ("t_basket");
CREATE INDEX idx_grundstueck_entstehung ON "grundstueck" ("entstehung");
CREATE INDEX idx_grundstueck_untergang ON "grundstueck" ("untergang");
CREATE INDEX idx_grundstueck_textposition_t_basket ON "grundstueck_textposition" ("t_basket");
CREATE INDEX idx_grundstueck_textposition_grundstueck_fk ON "grundstueck_textposition" ("grundstueck_fk");
-- NOTE (view grenzpunkt_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Geometrie' not in the CREATE VIEW: 'Geometrie' has no mapped column on table 'grenzpunkt'
-- NOTE (view grenzpunkt_gueltig): [SQL-VIEW-CONSTRAINT-DROPPED] VIEW-level UNIQUE 'CH040601' (Geometrie) - a CREATE VIEW cannot enforce it, and it is outside the single-base/plain-column subset a BEFORE INSERT/UPDATE trigger can
CREATE VIEW "grenzpunkt_gueltig" AS
    SELECT
        "grenzpunkt"."t_id" AS "t_id",
        "grenzpunkt"."t_ili_tid" AS "t_ili_tid",
        "grenzpunkt"."nbident" AS "nbident",
        "grenzpunkt"."nummer" AS "nummer",
        "grenzpunkt"."hoehengeometrie" AS "hoehengeometrie",
        "grenzpunkt"."lagegenauigkeit" AS "lagegenauigkeit",
        "grenzpunkt"."istlagezuverlaessig" AS "istlagezuverlaessig",
        "grenzpunkt"."hoehengenauigkeit" AS "hoehengenauigkeit",
        "grenzpunkt"."isthoehenzuverlaessig" AS "isthoehenzuverlaessig",
        "grenzpunkt"."punktzeichen" AS "punktzeichen",
        "grenzpunkt"."isthoheitsgrenzpunkt" AS "isthoheitsgrenzpunkt",
        "grenzpunkt"."isthoheitsgrenzsteinalt" AS "isthoheitsgrenzsteinalt",
        "grenzpunkt"."istexaktdefiniert" AS "istexaktdefiniert",
        "grenzpunkt"."symbolori" AS "symbolori"
    FROM "grenzpunkt" "grenzpunkt"
    WHERE ("grenzpunkt"."entstehung" IS NOT NULL)
      AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = "grenzpunkt"."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL))
      AND ((NOT ("grenzpunkt"."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = "grenzpunkt"."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))));
-- NOTE (view grundstueck_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Textposition' not in the CREATE VIEW: 'Textposition' has no mapped column on table 'grundstueck'
CREATE VIEW "grundstueck_gueltig" AS
    SELECT
        "grundstueck"."t_id" AS "t_id",
        "grundstueck"."t_ili_tid" AS "t_ili_tid",
        "grundstueck"."nbident" AS "nbident",
        "grundstueck"."nummer" AS "nummer",
        "grundstueck"."egrid" AS "egrid",
        "grundstueck"."iststreitig" AS "iststreitig",
        "grundstueck"."istvollstaendig" AS "istvollstaendig",
        "grundstueck"."grundstuecksart" AS "grundstuecksart",
        "grundstueck"."fiktiv" AS "fiktiv",
        "grundstueck"."gesamtflaechenmass" AS "gesamtflaechenmass"
    FROM "grundstueck" "grundstueck"
    WHERE ("grundstueck"."entstehung" IS NOT NULL)
      AND (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v1" WHERE "v1"."t_id" = "grundstueck"."entstehung" AND "v1"."grundbucheintrag" IS NOT NULL))
      AND ((NOT ("grundstueck"."untergang" IS NOT NULL)) OR (NOT (EXISTS (SELECT 1 FROM "gsnachfuehrung" "v2" WHERE "v2"."t_id" = "grundstueck"."untergang" AND "v2"."grundbucheintrag" IS NOT NULL))));
-- NOTE (view liegenschaft_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Geometrie' not in the CREATE VIEW: 'Geometrie' has no mapped column on table 'liegenschaft'
-- NOTE (view liegenschaft_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Streitig' not in the CREATE VIEW: 'Streitig' has no mapped column on table 'liegenschaft'
-- NOTE (view liegenschaft_gueltig): [SQL-VIEW-CONSTRAINT-DROPPED] VIEW-level SetConstraint 'CH041501' - a whole-population check no CREATE VIEW/TRIGGER can carry
CREATE VIEW "liegenschaft_gueltig" AS
    SELECT
        "liegenschaft"."t_id" AS "t_id",
        "liegenschaft"."t_ili_tid" AS "t_ili_tid",
        "liegenschaft"."nummerteilgrundstueck" AS "nummerteilgrundstueck",
        "liegenschaft"."fiktiv" AS "fiktiv",
        "liegenschaft"."flaechenmass" AS "flaechenmass",
        "liegenschaft"."qualitaetsstandard" AS "qualitaetsstandard"
    FROM "liegenschaft" "liegenschaft"
    WHERE (EXISTS (SELECT 1 FROM "grundstueck" "v1" WHERE "v1"."t_id" = "liegenschaft"."grundstueck" AND "v1"."entstehung" IS NOT NULL))
      AND (EXISTS (SELECT 1 FROM "grundstueck" "v2" WHERE "v2"."t_id" = "liegenschaft"."grundstueck" AND EXISTS (SELECT 1 FROM "gsnachfuehrung" "v3" WHERE "v3"."t_id" = "v2"."entstehung" AND "v3"."grundbucheintrag" IS NOT NULL)))
      AND ((NOT (EXISTS (SELECT 1 FROM "grundstueck" "v4" WHERE "v4"."t_id" = "liegenschaft"."grundstueck" AND "v4"."untergang" IS NOT NULL))) OR (NOT (EXISTS (SELECT 1 FROM "grundstueck" "v5" WHERE "v5"."t_id" = "liegenschaft"."grundstueck" AND EXISTS (SELECT 1 FROM "gsnachfuehrung" "v6" WHERE "v6"."t_id" = "v5"."untergang" AND "v6"."grundbucheintrag" IS NOT NULL)))));
-- NOTE (view selbstaendigesdauerndesrecht_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Geometrie' not in the CREATE VIEW: 'Geometrie' has no mapped column on table 'selbstaendigesdauerndesrecht'
-- NOTE (view selbstaendigesdauerndesrecht_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Streitig' not in the CREATE VIEW: 'Streitig' has no mapped column on table 'selbstaendigesdauerndesrecht'
CREATE VIEW "selbstaendigesdauerndesrecht_gueltig" AS
    SELECT
        "selbstaendigesdauerndesrecht"."t_id" AS "t_id",
        "selbstaendigesdauerndesrecht"."t_ili_tid" AS "t_ili_tid",
        "selbstaendigesdauerndesrecht"."nummerteilgrundstueck" AS "nummerteilgrundstueck",
        "selbstaendigesdauerndesrecht"."flaechenmass" AS "flaechenmass",
        "selbstaendigesdauerndesrecht"."istbaurecht" AS "istbaurecht"
    FROM "selbstaendigesdauerndesrecht" "selbstaendigesdauerndesrecht"
    WHERE (EXISTS (SELECT 1 FROM "grundstueck" "v1" WHERE "v1"."t_id" = "selbstaendigesdauerndesrecht"."grundstueck" AND "v1"."entstehung" IS NOT NULL))
      AND (EXISTS (SELECT 1 FROM "grundstueck" "v2" WHERE "v2"."t_id" = "selbstaendigesdauerndesrecht"."grundstueck" AND EXISTS (SELECT 1 FROM "gsnachfuehrung" "v3" WHERE "v3"."t_id" = "v2"."entstehung" AND "v3"."grundbucheintrag" IS NOT NULL)))
      AND ((NOT (EXISTS (SELECT 1 FROM "grundstueck" "v4" WHERE "v4"."t_id" = "selbstaendigesdauerndesrecht"."grundstueck" AND "v4"."untergang" IS NOT NULL))) OR (NOT (EXISTS (SELECT 1 FROM "grundstueck" "v5" WHERE "v5"."t_id" = "selbstaendigesdauerndesrecht"."grundstueck" AND EXISTS (SELECT 1 FROM "gsnachfuehrung" "v6" WHERE "v6"."t_id" = "v5"."untergang" AND "v6"."grundbucheintrag" IS NOT NULL)))));
-- NOTE (view bergwerk_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Geometrie' not in the CREATE VIEW: 'Geometrie' has no mapped column on table 'bergwerk'
-- NOTE (view bergwerk_gueltig): [SQL-VIEW-ATTR-DROPPED] attribute 'Streitig' not in the CREATE VIEW: 'Streitig' has no mapped column on table 'bergwerk'
CREATE VIEW "bergwerk_gueltig" AS
    SELECT
        "bergwerk"."t_id" AS "t_id",
        "bergwerk"."t_ili_tid" AS "t_ili_tid",
        "bergwerk"."nummerteilgrundstueck" AS "nummerteilgrundstueck",
        "bergwerk"."flaechenmass" AS "flaechenmass"
    FROM "bergwerk" "bergwerk"
    WHERE (EXISTS (SELECT 1 FROM "grundstueck" "v1" WHERE "v1"."t_id" = "bergwerk"."grundstueck" AND "v1"."entstehung" IS NOT NULL))
      AND (EXISTS (SELECT 1 FROM "grundstueck" "v2" WHERE "v2"."t_id" = "bergwerk"."grundstueck" AND EXISTS (SELECT 1 FROM "gsnachfuehrung" "v3" WHERE "v3"."t_id" = "v2"."entstehung" AND "v3"."grundbucheintrag" IS NOT NULL)))
      AND ((NOT (EXISTS (SELECT 1 FROM "grundstueck" "v4" WHERE "v4"."t_id" = "bergwerk"."grundstueck" AND "v4"."untergang" IS NOT NULL))) OR (NOT (EXISTS (SELECT 1 FROM "grundstueck" "v5" WHERE "v5"."t_id" = "bergwerk"."grundstueck" AND EXISTS (SELECT 1 FROM "gsnachfuehrung" "v6" WHERE "v6"."t_id" = "v5"."untergang" AND "v6"."grundbucheintrag" IS NOT NULL)))));
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
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Bergwerk', 'bergwerk');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt', 'grenzpunkt');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck', 'grundstueck');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Liegenschaft', 'liegenschaft');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.SelbstaendigesDauerndesRecht', 'selbstaendigesdauerndesrecht');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Liegenschaft.NummerTeilgrundstueck', 'nummerteilgrundstueck', 'liegenschaft', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Liegenschaft.Fiktiv', 'fiktiv', 'liegenschaft', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Liegenschaft.Flaechenmass', 'flaechenmass', 'liegenschaft', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Liegenschaft.Qualitaetsstandard', 'qualitaetsstandard', 'liegenschaft', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GrundstueckLiegenschaft.Grundstueck', 'grundstueck', 'liegenschaft', 'grundstueck');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.SelbstaendigesDauerndesRecht.NummerTeilgrundstueck', 'nummerteilgrundstueck', 'selbstaendigesdauerndesrecht', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.SelbstaendigesDauerndesRecht.Flaechenmass', 'flaechenmass', 'selbstaendigesdauerndesrecht', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.SelbstaendigesDauerndesRecht.IstBaurecht', 'istbaurecht', 'selbstaendigesdauerndesrecht', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GrundstueckSelbstaendigesDauerndesRecht.Grundstueck', 'grundstueck', 'selbstaendigesdauerndesrecht', 'grundstueck');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Bergwerk.NummerTeilgrundstueck', 'nummerteilgrundstueck', 'bergwerk', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Bergwerk.Flaechenmass', 'flaechenmass', 'bergwerk', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GrundstueckBergwerk.Grundstueck', 'grundstueck', 'bergwerk', 'grundstueck');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung.NBIdent', 'nbident', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung.Identifikator', 'identifikator', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung.Beschreibung', 'beschreibung', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung.Mutationsart', 'mutationsart', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung.GueltigerEintrag', 'gueltigereintrag', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung.Grundbucheintrag', 'grundbucheintrag', 'gsnachfuehrung', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.NBIdent', 'nbident', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.Nummer', 'nummer', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.Hoehengeometrie', 'hoehengeometrie', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.Lagegenauigkeit', 'lagegenauigkeit', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.IstLagezuverlaessig', 'istlagezuverlaessig', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.Hoehengenauigkeit', 'hoehengenauigkeit', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.IstHoehenzuverlaessig', 'isthoehenzuverlaessig', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.Punktzeichen', 'punktzeichen', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.IstHoheitsgrenzpunkt', 'isthoheitsgrenzpunkt', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.IstHoheitsgrenzsteinAlt', 'isthoheitsgrenzsteinalt', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.IstExaktDefiniert', 'istexaktdefiniert', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt.SymbolOri', 'symbolori', 'grenzpunkt', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Entstehung_Grenzpunkt.Entstehung', 'entstehung', 'grenzpunkt', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Untergang_Grenzpunkt.Untergang', 'untergang', 'grenzpunkt', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.NBIdent', 'nbident', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.Nummer', 'nummer', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.EGRID', 'egrid', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.IstStreitig', 'iststreitig', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.IstVollstaendig', 'istvollstaendig', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.Grundstuecksart', 'grundstuecksart', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.Fiktiv', 'fiktiv', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.Gesamtflaechenmass', 'gesamtflaechenmass', 'grundstueck', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Entstehung_Grundstueck.Entstehung', 'entstehung', 'grundstueck', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Untergang_Grundstueck.Untergang', 'untergang', 'grundstueck', 'gsnachfuehrung');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck.Textposition', 'grundstueck_fk', 'grundstueck_textposition', 'grundstueck');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAVTYM_Grafik_V1_0.Textposition.Orientierung', 'orientierung', 'grundstueck_textposition', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAVTYM_Grafik_V1_0.Textposition.DarstellungIn', 'darstellungin', 'grundstueck_textposition', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAVTYM_Grafik_V1_0.Textposition.Textgroesse', 'textgroesse', 'grundstueck_textposition', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAVTYM_Grafik_V1_0.Textposition.HReferenzpunkt', 'hreferenzpunkt', 'grundstueck_textposition', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('DMAVTYM_Grafik_V1_0.Textposition.VReferenzpunkt', 'vreferenzpunkt', 'grundstueck_textposition', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Bergwerk', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Entstehung_Grenzpunkt', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Entstehung_Grundstueck', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GSNachfuehrung', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grenzpunkt', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Grundstueck', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GrundstueckBergwerk', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GrundstueckLiegenschaft', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.GrundstueckSelbstaendigesDauerndesRecht', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Liegenschaft', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.SelbstaendigesDauerndesRecht', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Untergang_Grenzpunkt', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('DMAV_Grundstuecke_V1_1.Grundstuecke.Untergang_Grundstueck', NULL);
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('DMAV_Grundstuecke_V1_1.ili', '2.4', 'DMAV_Grundstuecke_V1_1{ Units GeometryCHLV95_V2 DMAVTYM_Geometrie_V1_0 DMAVTYM_Topologie_V1_0 DMAVTYM_Vermarkung_V1_0 DMAVTYM_Qualitaet_V1_0 DMAVTYM_Grafik_V1_0}', 'INTERLIS 2.4;

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!
!! Datenmodell der amtlichen Vermessung "Bund" (DMAV)
!! beschrieben in INTERLIS Version 2.4
!!
!! Bundesamt fuer Landestopographie (swisstopo)
!! Fachstelle Eidgenoessische Vermessungsdirektion (V+D)
!! CH-3084 Wabern
!! www.cadastre.ch und www.interlis.ch
!!
!! Version: 1 deutsch
!! Dateiname: DMAV_Grundstuecke_V1_1.ili
!!
!! Das vorliegende Datenmodell gilt fuer den Bezugsrahmen "Landesvermessung 1903+
!! (LV95)".
!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

!! Typ: A=Änderung, E=Ergänzung, L=Löschung
!!
!! Version    | Line  | Typ | Bemerkung
!!------------|-------|-----|-------------------------------------------------------------
!! 2026-01-31 | 98-102|  L  | ASSOCIATION Vorgaenger_Nachfolger_Grenzpunkt
!! 2026-01-31 |142-146|  L  | ASSOCIATION Vorgaenger_Nachfolger_Grundstueck
!! 2026-01-31 |    193|  E  | IstBaurecht: BOOLEAN;

!!@ technicalContact = mailto:dmav@swisstopo.ch
!!@ furtherInformation = https://www.cadastre-manual.admin.ch/de/modelldokumentation-dmav
!!@ IDGeoIV = "228.4"
MODEL DMAV_Grundstuecke_V1_1 (de)
  AT "https://models.geo.admin.ch/V_D/" VERSION "2026-01-31" =
  IMPORTS Units;
  IMPORTS GeometryCHLV95_V2;
  IMPORTS DMAVTYM_Geometrie_V1_0;
  IMPORTS DMAVTYM_Topologie_V1_0;
  IMPORTS DMAVTYM_Vermarkung_V1_0;
  IMPORTS DMAVTYM_Qualitaet_V1_0;
  IMPORTS DMAVTYM_Grafik_V1_0;

  TOPIC Grundstuecke =
    BASKET OID AS INTERLIS.UUIDOID;
    OID AS INTERLIS.UUIDOID;

    DOMAIN

      Grundstuecksart = (
        Liegenschaft,
        SelbstaendigesDauerndesRecht,
        Bergwerk);
        
      Mutationsart = (
    	  Normal, 
    	  Projektmutation, 
    	  AbschlussProjektmutation);

    CLASS GSNachfuehrung =
      NBIdent: MANDATORY TEXT*12;
      Identifikator: MANDATORY TEXT*12;  !! z.B. Nummer des technischen Dossiers
      Beschreibung: MANDATORY TEXT*60;
      Perimeter:  SURFACE WITH (STRAIGHTS, ARCS) VERTEX GeometryCHLV95_V2.Coord2
        WITHOUT OVERLAPS > 0.002;
      Mutationsart: MANDATORY Mutationsart;
      GueltigerEintrag: MANDATORY INTERLIS.XMLDateTime; !! Techn. Bearbeitung
      Grundbucheintrag: INTERLIS.XMLDateTime;
    UNIQUE CH040101: NBIdent, Identifikator;
    END GSNachfuehrung;

    !! Umfasst alle Grenzpunkte einer Liegenschaft, welche Liegenschafts-
    !! oder Hoheitsgrenzpunkte sind.
    CLASS Grenzpunkt =
      NBIdent: TEXT*12;
      Nummer:  TEXT*12;
      Geometrie: MANDATORY GeometryCHLV95_V2.Coord2;
      Hoehengeometrie: DMAVTYM_Geometrie_V1_0.Hoehe;
      Lagegenauigkeit: MANDATORY DMAVTYM_Qualitaet_V1_0.Genauigkeit;
      IstLagezuverlaessig: MANDATORY DMAVTYM_Qualitaet_V1_0.Zuverlaessigkeit;
      Hoehengenauigkeit: DMAVTYM_Qualitaet_V1_0.Genauigkeit;
      IstHoehenzuverlaessig: DMAVTYM_Qualitaet_V1_0.Zuverlaessigkeit; 
      Punktzeichen: MANDATORY DMAVTYM_Vermarkung_V1_0.Versicherungsart; 
      IstHoheitsgrenzpunkt: MANDATORY BOOLEAN;
      IstHoheitsgrenzsteinAlt: MANDATORY BOOLEAN;
      IstExaktDefiniert: MANDATORY BOOLEAN;
      SymbolOri: DMAVTYM_Grafik_V1_0.Rotation; !! // undefiniert = 0.0 //
      MANDATORY CONSTRAINT CH040201: DEFINED(Hoehengeometrie)==DEFINED(Hoehengenauigkeit);
      MANDATORY CONSTRAINT CH040202: DEFINED(Hoehengeometrie)==DEFINED(IstHoehenzuverlaessig);
      MANDATORY CONSTRAINT CH040203: IstExaktDefiniert OR Punktzeichen==#unversichert;
    END Grenzpunkt;

    ASSOCIATION Entstehung_Grenzpunkt =
      Entstehung -- {1} GSNachfuehrung;
      entstehender_Grenzpunkt -- {0..*} Grenzpunkt;
    END Entstehung_Grenzpunkt;

    ASSOCIATION Untergang_Grenzpunkt =
      Untergang -- {0..1} GSNachfuehrung;
      untergehender_Grenzpunkt -- {0..*} Grenzpunkt;
    END Untergang_Grenzpunkt;

    VIEW Grenzpunkt_Gueltig
     	PROJECTION OF Grenzpunkt;
    	WHERE DEFINED(Grenzpunkt->Entstehung) AND DEFINED(Grenzpunkt->Entstehung->Grundbucheintrag) AND (NOT(DEFINED(Grenzpunkt->Untergang)) OR NOT(DEFINED(Grenzpunkt->Untergang->Grundbucheintrag)));
    	=
    	ALL OF Grenzpunkt;
    UNIQUE CH040601: Geometrie;
    END Grenzpunkt_Gueltig;
    
    CLASS Grundstueck =
      NBIdent: MANDATORY TEXT*12;
      Nummer: MANDATORY TEXT*12;
      !! Elektronisches Grundstueckinformationssystem
      EGRID:  TEXT*14;
      !! abgeleitetes Attribut: muss streitig sein, falls Liegenschaft,
      !! SelbstRecht oder Bergwerk streitig;
      IstStreitig: MANDATORY BOOLEAN;
      !!unvollstaendig, falls z.B. das Grundstueck
      !! teilweise ausserhalb des Perimeters liegt.
      IstVollstaendig: MANDATORY BOOLEAN;
      Grundstuecksart: MANDATORY Grundstuecksart;
      Textposition: BAG {0..*} OF DMAVTYM_Grafik_V1_0.Textposition;
      Fiktiv : MANDATORY BOOLEAN;
      !! Gesamtflaechenmass wird nur benutzt, falls TeilGrundstueke existieren.
      !! Das heisst mehrere Objekte Liegenschaft, SelbstRecht
      !! oder Bergwerk werden zu einem Objekt Grundstueck.
      Gesamtflaechenmass:  1 .. 999999999 [Units.m2];
      MANDATORY CONSTRAINT CH040701: IstVollstaendig == NOT(DEFINED(Gesamtflaechenmass));
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
    	WHERE DEFINED(Grundstueck->Entstehung) AND DEFINED(Grundstueck->Entstehung->Grundbucheintrag) AND (NOT(DEFINED(Grundstueck->Untergang)) OR NOT(DEFINED(Grundstueck->Untergang->Grundbucheintrag)));
    	=
    	ALL OF Grundstueck;
     UNIQUE CH041101: NBIdent, Nummer;
     UNIQUE CH041102: EGRID;
    END Grundstueck_Gueltig;

    CLASS Liegenschaft =
      !! NummerTeilgrundstueck ist fuer Teil Grundstueck noetig
      NummerTeilgrundstueck:  TEXT*12; 
      Geometrie: MANDATORY SURFACE WITH (STRAIGHTS, ARCS) VERTEX GeometryCHLV95_V2.Coord2
        !! // Geometrie nur LFP3 oder Grenzpunkt //
        WITHOUT OVERLAPS > 0.002;
      Fiktiv : MANDATORY BOOLEAN;
      Flaechenmass: MANDATORY 1 .. 999999999 [Units.m2];
      Streitig: GeometryCHLV95_V2.MultiLine;
      Qualitaetsstandard: MANDATORY DMAVTYM_Qualitaet_V1_0.Qualitaetsstandard;
      !! Verlauf einer streitigen Grenze muss auf derselben Liegenschaftsgeometrie liegen
      MANDATORY CONSTRAINT CH041201: NOT(DEFINED(Streitig)) OR DMAVTYM_Topologie_V1_0.covers(THIS,>>Geometrie,THIS,>>Streitig);
    END Liegenschaft;

    ASSOCIATION GrundstueckLiegenschaft =
      Grundstueck -<#> {1} Grundstueck;
      Liegenschaft -- {0..*} Liegenschaft;
      MANDATORY CONSTRAINT CH041301: Grundstueck->Grundstuecksart == #Liegenschaft;        
      MANDATORY CONSTRAINT CH041302: Liegenschaft->Fiktiv==Grundstueck->Fiktiv;        
    END GrundstueckLiegenschaft;
    
    CONSTRAINTS OF Liegenschaft =
      MANDATORY CONSTRAINT CH041401: DEFINED(Streitig)==Grundstueck->IstStreitig;        
      MANDATORY CONSTRAINT CH041402: NOT(DEFINED(NummerTeilgrundstueck)) == Grundstueck->IstVollstaendig;
    END;
    
    VIEW Liegenschaft_Gueltig
     	PROJECTION OF Liegenschaft;
    	WHERE DEFINED(Liegenschaft->Grundstueck->Entstehung) AND DEFINED(Liegenschaft->Grundstueck->Entstehung->Grundbucheintrag) AND (NOT(DEFINED(Liegenschaft->Grundstueck->Untergang)) OR NOT(DEFINED(Liegenschaft->Grundstueck->Untergang->Grundbucheintrag)));
    	=
    	ALL OF Liegenschaft;
    SET CONSTRAINT CH041501: INTERLIS.areAreas(ALL, UNDEFINED, >> Geometrie);
    END Liegenschaft_Gueltig;
    
    !! Falls bei SelbstRecht keine Flaeche vorhanden ist, existiert hier
    !! auch kein Objekt.
    CLASS SelbstaendigesDauerndesRecht =
      !! NummerTeilgrundstueck ist fuer Teil Grundstueck noetig
      NummerTeilgrundstueck:  TEXT*12; 
      Geometrie: MANDATORY SURFACE WITH (STRAIGHTS, ARCS) VERTEX GeometryCHLV95_V2.Coord2
        !! // Geometrie nur LFP3 oder Grenzpunkt //
        WITHOUT OVERLAPS > 0.002;
      Flaechenmass: MANDATORY 1 .. 999999999 [Units.m2];
      Streitig: GeometryCHLV95_V2.MultiLine;
      IstBaurecht: BOOLEAN;
      !! Verlauf einer streitigen Grenze muss auf derselben SelbstRechtgeometrie liegen
      MANDATORY CONSTRAINT CH041601: NOT(DEFINED(Streitig)) OR DMAVTYM_Topologie_V1_0.covers(THIS,>>Geometrie,THIS,>>Streitig);
    END SelbstaendigesDauerndesRecht;

    ASSOCIATION GrundstueckSelbstaendigesDauerndesRecht =
      Grundstueck -<#> {1} Grundstueck;
      SelbstaendigesDauerndesRecht-- {0..*} SelbstaendigesDauerndesRecht;
      MANDATORY CONSTRAINT CH041701: Grundstueck->Grundstuecksart == #SelbstaendigesDauerndesRecht;
      MANDATORY CONSTRAINT CH041702: NOT(Grundstueck->Fiktiv);
	 END GrundstueckSelbstaendigesDauerndesRecht;
    
    CONSTRAINTS OF SelbstaendigesDauerndesRecht =
      MANDATORY CONSTRAINT CH041801: DEFINED(Streitig)==Grundstueck->IstStreitig;        
      MANDATORY CONSTRAINT CH041802: NOT(DEFINED(NummerTeilgrundstueck)) == Grundstueck->IstVollstaendig;
    END;
    
    VIEW SelbstaendigesDauerndesRecht_Gueltig
     	PROJECTION OF SelbstaendigesDauerndesRecht;
    	WHERE DEFINED(SelbstaendigesDauerndesRecht->Grundstueck->Entstehung) AND DEFINED(SelbstaendigesDauerndesRecht->Grundstueck->Entstehung->Grundbucheintrag) AND (NOT(DEFINED(SelbstaendigesDauerndesRecht->Grundstueck->Untergang)) OR NOT(DEFINED(SelbstaendigesDauerndesRecht->Grundstueck->Untergang->Grundbucheintrag)));
    	=
    	ALL OF SelbstaendigesDauerndesRecht;
    END SelbstaendigesDauerndesRecht_Gueltig;
    
    !! Falls bei Bergwerk keine Flaeche vorhanden ist, existiert hier
    !! auch kein Objekt.
    CLASS Bergwerk =
      !! NummerTeilGrundstueck ist fuer Teil Grundstueck noetig
      NummerTeilgrundstueck:  TEXT*12;
      Geometrie: MANDATORY SURFACE WITH (STRAIGHTS, ARCS) VERTEX GeometryCHLV95_V2.Coord2
        !! // Geometrie nur LFP3 oder Grenzpunkt //
        WITHOUT OVERLAPS > 0.002;
      Flaechenmass: MANDATORY 1 .. 999999999 [Units.m2];
      Streitig: GeometryCHLV95_V2.MultiLine;
      !! Verlauf einer streitigen Grenze muss auf derselben Bergwerkgeometrie liegen
      MANDATORY CONSTRAINT CH042001: NOT(DEFINED(Streitig)) OR DMAVTYM_Topologie_V1_0.covers(THIS,>>Geometrie,THIS,>>Streitig);
    END Bergwerk;

    ASSOCIATION GrundstueckBergwerk =
      Grundstueck -<#> {1} Grundstueck;
      Bergwerk -- {0..*} Bergwerk;
      MANDATORY CONSTRAINT CH042101: Grundstueck->Grundstuecksart == #Bergwerk;
      MANDATORY CONSTRAINT CH042102: NOT(Grundstueck->Fiktiv);
    END GrundstueckBergwerk;
    
    CONSTRAINTS OF Bergwerk =
      MANDATORY CONSTRAINT CH042201: DEFINED(Streitig)==Grundstueck->IstStreitig;        
      MANDATORY CONSTRAINT CH042202: NOT(DEFINED(NummerTeilgrundstueck)) == Grundstueck->IstVollstaendig;
    END;

    VIEW Bergwerk_Gueltig
     	PROJECTION OF Bergwerk;
    	WHERE DEFINED(Bergwerk->Grundstueck->Entstehung) AND DEFINED(Bergwerk->Grundstueck->Entstehung->Grundbucheintrag) AND (NOT(DEFINED(Bergwerk->Grundstueck->Untergang)) OR NOT(DEFINED(Bergwerk->Grundstueck->Untergang->Grundbucheintrag)));
    	=
    	ALL OF Bergwerk;
    END Bergwerk_Gueltig;

    CONSTRAINTS OF Grundstueck =
      MANDATORY CONSTRAINT CH042401: DEFINED(Gesamtflaechenmass) ==
      	((INTERLIS.objectCount(Liegenschaft) + INTERLIS.objectCount(SelbstaendigesDauerndesRecht) +
      		INTERLIS.objectCount(Bergwerk))>1);
      MANDATORY CONSTRAINT CH042402:
      	(INTERLIS.objectCount(Liegenschaft)>0 AND INTERLIS.objectCount(SelbstaendigesDauerndesRecht)==0 AND INTERLIS.objectCount(Bergwerk)==0)
      	OR (INTERLIS.objectCount(Liegenschaft)==0 AND INTERLIS.objectCount(SelbstaendigesDauerndesRecht)>0 AND INTERLIS.objectCount(Bergwerk)==0)
      	OR (INTERLIS.objectCount(Liegenschaft)==0 AND INTERLIS.objectCount(SelbstaendigesDauerndesRecht)==0 AND INTERLIS.objectCount(Bergwerk)>0);
    END;
    
  END Grundstuecke;
 
END DMAV_Grundstuecke_V1_1.
', now());
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('DMAVTYM_Geometrie_V1_0.ili', '2.4', 'DMAVTYM_Geometrie_V1_0', 'INTERLIS 2.4;

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!
!! Datenmodell der amtlichen Vermessung "Bund" (DMAV)
!! beschrieben in INTERLIS Version 2.4
!!
!! Bundesamt fuer Landestopographie (swisstopo)
!! Fachstelle Eidgenoessische Vermessungsdirektion (V+D)
!! CH-3084 Wabern
!! www.cadastre.ch und www.interlis.ch
!!
!! Version: 1 deutsch
!! Dateiname: DMAVTYM_Geometrie_V1_0.ili
!!
!! Das vorliegende Datenmodell gilt fuer den Bezugsrahmen "Landesvermessung 1903+
!! (LV95)".
!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

!!@ technicalContact = mailto:dmav@swisstopo.ch
!!@ furtherInformation = https://www.cadastre-manual.admin.ch/de/modelldokumentation-dmav
TYPE MODEL DMAVTYM_Geometrie_V1_0 (de)
  AT "https://models.geo.admin.ch/V_D/" VERSION "2024-05-15" =
  
  DOMAIN
  
    Hoehe = -200.000 ..   5000.000 [INTERLIS.m];
  
END DMAVTYM_Geometrie_V1_0.
', now());
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('DMAVTYM_Topologie_V1_0.ili', '2.4', 'DMAVTYM_Topologie_V1_0', 'INTERLIS 2.4;

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!
!! Datenmodell der amtlichen Vermessung "Bund" (DMAV)
!! beschrieben in INTERLIS Version 2.4
!!
!! Bundesamt fuer Landestopographie (swisstopo)
!! Fachstelle Eidgenoessische Vermessungsdirektion (V+D)
!! CH-3084 Wabern
!! www.cadastre.ch und www.interlis.ch
!!
!! Version: 1 deutsch
!! Dateiname: DMAVTYM_Topologie_V1_0.ili
!!
!! Das vorliegende Datenmodell gilt fuer den Bezugsrahmen "Landesvermessung 1903+
!! (LV95)".
!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

!!@ technicalContact = mailto:dmav@swisstopo.ch
!!@ furtherInformation = https://www.cadastre-manual.admin.ch/de/modelldokumentation-dmav
TYPE MODEL DMAVTYM_Topologie_V1_0 (de)
  AT "https://models.geo.admin.ch/V_D/" VERSION "2024-05-15" =
  
  FUNCTION covers(
    SurfaceObjects: CLASS;
    SurfaceAttr: ATTRIBUTE;
    MultiLineObject: ANYSTRUCTURE;
    MultiLineAttr: ATTRIBUTE): BOOLEAN;
  
END DMAVTYM_Topologie_V1_0.
', now());
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('DMAVTYM_Vermarkung_V1_0.ili', '2.4', 'DMAVTYM_Vermarkung_V1_0', 'INTERLIS 2.4;

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!
!! Datenmodell der amtlichen Vermessung "Bund" (DMAV)
!! beschrieben in INTERLIS Version 2.4
!!
!! Bundesamt fuer Landestopographie (swisstopo)
!! Fachstelle Eidgenoessische Vermessungsdirektion (V+D)
!! CH-3084 Wabern
!! www.cadastre.ch und www.interlis.ch
!!
!! Version: 1 deutsch
!! Dateiname: DMAVTYM_Vermarkung_V1_0.ili
!!
!! Das vorliegende Datenmodell gilt fuer den Bezugsrahmen "Landesvermessung 1903+
!! (LV95)".
!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

!!@ technicalContact = mailto:dmav@swisstopo.ch
!!@ furtherInformation = https://www.cadastre-manual.admin.ch/de/modelldokumentation-dmav
TYPE MODEL DMAVTYM_Vermarkung_V1_0 (de)
  AT "https://models.geo.admin.ch/V_D/" VERSION "2024-05-15" =

  DOMAIN

    Versicherungsart = (
      Stein,
      Kunststoffzeichen,
      Bolzen,
      Rohr,
      Pfahl,
      Kreuz,
      unversichert,
      weitere);

END DMAVTYM_Vermarkung_V1_0.
', now());
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('DMAVTYM_Qualitaet_V1_0.ili', '2.4', 'DMAVTYM_Qualitaet_V1_0{ Units}', 'INTERLIS 2.4;

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!
!! Datenmodell der amtlichen Vermessung "Bund" (DMAV)
!! beschrieben in INTERLIS Version 2.4
!!
!! Bundesamt fuer Landestopographie (swisstopo)
!! Fachstelle Eidgenoessische Vermessungsdirektion (V+D)
!! CH-3084 Wabern
!! www.cadastre.ch und www.interlis.ch
!!
!! Version: 1 deutsch
!! Dateiname: DMAVTYM_Qualitaet_V1_0.ili
!!
!! Das vorliegende Datenmodell gilt fuer den Bezugsrahmen "Landesvermessung 1903+
!! (LV95)".
!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

!!@ technicalContact = mailto:dmav@swisstopo.ch
!!@ furtherInformation = https://www.cadastre-manual.admin.ch/de/modelldokumentation-dmav
TYPE MODEL DMAVTYM_Qualitaet_V1_0 (de)
  AT "https://models.geo.admin.ch/V_D/" VERSION "2024-05-15" =
  IMPORTS Units;
  
  DOMAIN
  
    Genauigkeit = 0.001 .. 7.000 [INTERLIS.m];

    Zuverlaessigkeit = BOOLEAN;

    Qualitaetsstandard = (
      AV93,
      PN);
      
END DMAVTYM_Qualitaet_V1_0.
', now());
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('DMAVTYM_Grafik_V1_0.ili', '2.4', 'DMAVTYM_Grafik_V1_0{ Units GeometryCHLV95_V2}', 'INTERLIS 2.4;

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!!
!! Datenmodell der amtlichen Vermessung "Bund" (DMAV)
!! beschrieben in INTERLIS Version 2.4
!!
!! Bundesamt fuer Landestopographie (swisstopo)
!! Fachstelle Eidgenoessische Vermessungsdirektion (V+D)
!! CH-3084 Wabern
!! www.cadastre.ch und www.interlis.ch
!!
!! Version: 1 deutsch
!! Dateiname: DMAVTYM_Grafik_V1_0.ili
!!
!! Das vorliegende Datenmodell gilt fuer den Bezugsrahmen "Landesvermessung 1903+
!! (LV95)".
!!
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

!!@ technicalContact = mailto:dmav@swisstopo.ch
!!@ furtherInformation = https://www.cadastre-manual.admin.ch/de/modelldokumentation-dmav
TYPE MODEL DMAVTYM_Grafik_V1_0 (de)
  AT "https://models.geo.admin.ch/V_D/" VERSION "2024-05-15" =
  IMPORTS Units;
  IMPORTS GeometryCHLV95_V2;
  
  DOMAIN
  
    Rotation = 0.0 .. 399.9 [Units.Gon];

    DarstellungIn = (PlanFuerDasGrundbuch, Basisplan);

    Schriftgroesse = (Klein, Mittel, Gross);
    
  STRUCTURE Textposition =
  	Position: MANDATORY GeometryCHLV95_V2.Coord2;
  	Orientierung: Rotation;
	  DarstellungIn: DarstellungIn;
	  Textgroesse: Schriftgroesse;
	  HReferenzpunkt: HALIGNMENT;
	  VReferenzpunkt: VALIGNMENT;
	  Hinweisstrich: GeometryCHLV95_V2.LineWithoutArcs;
  END Textposition;
  
  STRUCTURE Symbolposition =
  	Position: MANDATORY GeometryCHLV95_V2.Coord2;
  	Orientierung: Rotation;
	  DarstellungIn: DarstellungIn;
  END Symbolposition;
  
END DMAVTYM_Grafik_V1_0.
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
