CREATE TABLE "person" (
    "id" text PRIMARY KEY,
    "name" text NOT NULL,
    "birthyear" integer,
    "kind" text,
    "employer" text,
    CONSTRAINT chk_person_birthyear_domain CHECK ("birthyear" BETWEEN 1800 AND 2100),
    CONSTRAINT chk_person_kind_domain CHECK ("kind" IN ('Adult', 'Child'))
);
CREATE TABLE "company" (
    "id" text PRIMARY KEY,
    "name" text NOT NULL
);
ALTER TABLE "person" ADD CONSTRAINT fk_person_employer FOREIGN KEY ("employer") REFERENCES "company" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_person_employer ON "person" ("employer");
