CREATE TABLE "person" (
    "id" text PRIMARY KEY,
    "name" text NOT NULL,
    "birthyear" integer,
    "kind" text,
    "employer" text
);
CREATE TABLE "company" (
    "id" text PRIMARY KEY,
    "name" text NOT NULL
);
ALTER TABLE "person" ADD CONSTRAINT fk_person_employer FOREIGN KEY ("employer") REFERENCES "company" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_person_employer ON "person" ("employer");
