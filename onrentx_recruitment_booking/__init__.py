from . import models
from . import controllers
from . import wizard


def post_init_seed_course_maps(env):
    """Plan 10-05 F7 fix: idempotent seed of onboarding course maps.

    Runs on install AND upgrade (replaces the broken <function> XML that
    ran once on first install and never again). Uses ORM search/create so
    it's safe to re-run.

    Course IDs are resolved by name (not hardcoded) so this seed survives
    DB rebuilds where the auto-incremented IDs may differ.
    """
    import logging

    _logger = logging.getLogger(__name__)

    CourseMap = env["onrentx.onboarding.course.map"].sudo()
    Job = env["hr.job"].sudo()
    Channel = env["slide.channel"].sudo()

    def _channel_id(name_pattern):
        ch = Channel.search([("name", "ilike", name_pattern)], limit=1)
        return ch.id if ch else False

    BASE_ID = _channel_id("Onboarding Base OnRentX")
    DEV_ID = _channel_id("Onboarding Dev OnRentX")
    PROY_ID = _channel_id("Onboarding Proyectos OnRentX")
    ASIS_ID = _channel_id("Onboarding Asistente OnRentX")

    if not BASE_ID:
        _logger.warning(
            "Plan 10-05 post_init_seed_course_maps: base course "
            "'Onboarding Base OnRentX' not found, skipping seed entirely"
        )
        return

    seeds = [
        ("Developer", [BASE_ID], [DEV_ID] if DEV_ID else [], "exercises"),
        ("Proyecto", [BASE_ID], [PROY_ID] if PROY_ID else [], "both"),
        ("Asistente", [BASE_ID], [ASIS_ID] if ASIS_ID else [], "trial_week"),
    ]
    for name_pattern, base_ids, specific_ids, mode in seeds:
        job = Job.search([("name", "ilike", name_pattern)], limit=1)
        if not job:
            _logger.info(
                "Plan 10-05 seed: job matching %r not found, skipping course map",
                name_pattern,
            )
            continue
        if CourseMap.search([("job_id", "=", job.id)], limit=1):
            _logger.info(
                "Plan 10-05 seed: course map for job %s already exists, skipping",
                job.name,
            )
            continue
        CourseMap.create(
            {
                "job_id": job.id,
                "base_course_ids": [(6, 0, base_ids)],
                "specific_course_ids": [(6, 0, specific_ids)],
                "evaluation_mode": mode,
                "trial_week_days": 5,
            }
        )
        _logger.info(
            "Plan 10-05 seed: created course map for job %s (mode=%s, "
            "base=%s, specific=%s)",
            job.name,
            mode,
            base_ids,
            specific_ids,
        )
