CREATE TABLE "wegabschnitt" (
    "id" text PRIMARY KEY,
    "bezeichnung" varchar(40) NOT NULL,
    "kategorie" text NOT NULL,
    "belagsart" varchar(20),
    CONSTRAINT chk_wegabschnitt_kategorie_domain CHECK ("kategorie" IN ('AlpinWanderweg', 'Bergwanderweg', 'Wanderweg'))
);
CREATE TABLE "wegweiser" (
    "id" text PRIMARY KEY,
    "standort" varchar(40) NOT NULL,
    "hoehe" integer,
    "wegabschnitt" text NOT NULL,
    CONSTRAINT chk_wegweiser_hoehe_domain CHECK ("hoehe" BETWEEN 0 AND 5000)
);
ALTER TABLE "wegweiser" ADD CONSTRAINT fk_wegweiser_wegabschnitt FOREIGN KEY ("wegabschnitt") REFERENCES "wegabschnitt" ("id") DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX idx_wegweiser_wegabschnitt ON "wegweiser" ("wegabschnitt");
CREATE VIEW "wegabschnitt_mitwegweiser" AS
    SELECT
        "wegabschnitt"."id" AS "id",
        "wegabschnitt"."bezeichnung" AS "bezeichnung",
        "wegabschnitt"."kategorie" AS "kategorie",
        "wegabschnitt"."belagsart" AS "belagsart"
    FROM "wegabschnitt" "wegabschnitt"
    WHERE (EXISTS (SELECT 1 FROM "wegweiser" "v1" WHERE "v1"."wegabschnitt" = "wegabschnitt"."id"));
