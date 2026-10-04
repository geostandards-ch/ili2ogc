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
CREATE TABLE "person" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "name" text NOT NULL,
    "birthyear" integer,
    "kind" text,
    "employer" bigint,
    CONSTRAINT chk_person_birthyear_domain CHECK ("birthyear" BETWEEN 1800 AND 2100),
    CONSTRAINT chk_person_kind_domain CHECK ("kind" IN ('Adult', 'Child'))
);
CREATE TABLE "company" (
    "t_id" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq'),
    "t_basket" bigint NOT NULL,
    "t_ili_tid" varchar(200),
    "name" text NOT NULL
);
ALTER TABLE "person" ADD CONSTRAINT person_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "person" ADD CONSTRAINT fk_person_employer FOREIGN KEY ("employer") REFERENCES "company" ("t_id") DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE "company" ADD CONSTRAINT company_t_basket_fkey FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_person_t_basket ON "person" ("t_basket");
CREATE INDEX idx_person_employer ON "person" ("employer");
CREATE INDEX idx_company_t_basket ON "company" ("t_basket");
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('MinimalTest.MainTopic.Company', 'company');
INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('MinimalTest.MainTopic.Person', 'person');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('MinimalTest.MainTopic.Person.Name', 'name', 'person', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('MinimalTest.MainTopic.Person.BirthYear', 'birthyear', 'person', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('MinimalTest.MainTopic.Person.Kind', 'kind', 'person', NULL);
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('MinimalTest.MainTopic.WorksFor.Employer', 'employer', 'person', 'company');
INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('MinimalTest.MainTopic.Company.Name', 'name', 'company', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('MinimalTest.MainTopic.Company', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('MinimalTest.MainTopic.Person', NULL);
INSERT INTO T_ILI2DB_INHERITANCE (thisClass, baseClass) VALUES ('MinimalTest.MainTopic.WorksFor', NULL);
INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('minimal_model.ili', '2.4', 'MinimalTest', 'INTERLIS 2.4;

MODEL MinimalTest AT "https://example.org/minimal" VERSION "2026-08-02" =

  TOPIC MainTopic =

    CLASS Person =
      Name: MANDATORY TEXT;
      BirthYear: 1800 .. 2100;
      Kind: (Adult, Child);
    END Person;

    CLASS Company =
      Name: MANDATORY TEXT;
    END Company;

    ASSOCIATION WorksFor =
      Employee -- {0..*} Person;
      Employer (EXTENDED) -- {0..1} Company;
    END WorksFor;

  END MainTopic;

END MinimalTest.
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
