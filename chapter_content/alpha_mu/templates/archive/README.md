# Alpha Mu archive pages (chapter-specific content)

Moved here from `templates/archive/` on 09-27-26 (multi-chapter phase 2): officer duties, advisors, academic standards (the Samford AMA), committee details, Kai procedures and slating/elections are Alpha Mu's own content, so they live only in Alpha Mu's content directory and no other chapter can see them.

**They are not routed.** Their views in `src/view/archive/` have not been imported by `src/urls.py` since v3.0.0, and the `{% url %}` names they use (`officer_duties_detail`, `kai_procedures_detail`, …) do not exist. So nobody can reach these pages today, Alpha Mu included. They are kept as reference text. If they are ever brought back, route them from Alpha Mu's config only.

`constitution_bylaws.html` joined them on 09-28-26: the original hand-written static C&B page. Its view (`src/view/constitution_bylaws.py`) had been imported by `src/urls.py` but not routed since the C&B moved into the database; `/constitution-bylaws/` is `cnb_viewer`. The view was deleted; the page is kept here as reference text.
