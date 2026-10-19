package cylist.app.docs

import cylist.api.generated.models.Alternative
import cylist.api.generated.models.DocAnswer
import cylist.api.generated.models.DocTree
import cylist.api.generated.models.SectionFound
import java.math.BigDecimal
import java.net.URLEncoder
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale
import java.util.UUID
import kotlin.math.roundToInt

/*
 * How the Docs screen words and orders what the server sends, kept apart from
 * Compose so it can be tested. Each rule is a port of the web's, named beside
 * it: frontend/src/routes/docs/AnswerView.tsx and ProjectDocs.tsx.
 */

/** How an answer's status is said, and in which tone (AnswerView.tsx `STATUS_LABEL` / `STATUS_CLASS`). */
enum class Tone { Ok, Hold, Blocked }

fun statusLabel(status: DocAnswer.Status): String = when (status) {
    DocAnswer.Status.ok -> "Answered"
    DocAnswer.Status.ambiguous -> "Answered — a close call"
    DocAnswer.Status.unverified -> "Best guess only"
    DocAnswer.Status.not_documented -> "Not in the docs"
    DocAnswer.Status.unavailable -> "Could not ask"
}

fun statusTone(status: DocAnswer.Status): Tone = when (status) {
    DocAnswer.Status.ok, DocAnswer.Status.ambiguous -> Tone.Ok
    DocAnswer.Status.unverified, DocAnswer.Status.not_documented -> Tone.Hold
    DocAnswer.Status.unavailable -> Tone.Blocked
}

/** Whether the docs answered: only then is the found section drawn as the answer. */
fun answered(answer: DocAnswer): Boolean =
    answer.status == DocAnswer.Status.ok || answer.status == DocAnswer.Status.ambiguous

/** "86% relevant", or null when jev gave no figure. */
fun percent(relevance: BigDecimal?): String? = relevance?.let { "${(it.toDouble() * 100).roundToInt()}% relevant" }

/**
 * Below this, a section jev read and set aside is not worth a line beside an
 * answer: it was weighed for its title and found to be about something else.
 */
private const val WORTH_A_LOOK = 0.3

/** A section the answer view lists under "Also close" or "Sections it weighed". */
data class Weighed(val docId: UUID, val path: String, val section: String?, val relevance: BigDecimal?)

/**
 * Beside an answer, only the near misses. Without one, every section that was
 * weighed, the best guess first; its text is not shown, since a section that
 * failed the check drawn as a page reads as though it answered.
 */
fun weighed(answer: DocAnswer): List<Weighed> {
    val others = answer.alternatives.map(Alternative::toWeighed)
    return if (answered(answer)) {
        others.filter { (it.relevance?.toDouble() ?: 0.0) >= WORTH_A_LOOK }
    } else {
        listOfNotNull(answer.found?.toWeighed()) + others
    }
}

/** The heading over [weighed]. */
fun weighedHeading(answer: DocAnswer): String = if (answered(answer)) "Also close" else "Sections it weighed"

private fun Alternative.toWeighed() = Weighed(docId, path, section, relevance)

private fun SectionFound.toWeighed() = Weighed(docId, path, section, relevance)

/** Why no answer was shown, in the words the status calls for. */
fun missExplained(answer: DocAnswer): String = when (answer.status) {
    DocAnswer.Status.unavailable -> "The docs could not be asked just now: ${answer.reason ?: "jev did not answer"}."
    DocAnswer.Status.unverified ->
        "No section passed the relevance check, so nothing here is sure to answer it. " +
            "An agent that works it out from the code writes it into the docs."
    else ->
        "Nothing in the docs answers this yet. An agent that works it out from the " +
            "code writes it into the docs, so the next time it is asked it is here."
}

/** Whether "How it was routed" has anything to say. */
fun routing(answer: DocAnswer): String? = answer.reason?.takeIf { answer.status != DocAnswer.Status.unavailable }

/** `Engineering / APIs / Webhooks` and a section, as the steps of a breadcrumb. */
fun crumbs(path: String, section: String?): List<String> = path.split(" / ") + listOfNotNull(section)

/** A found section's breadcrumb: the doc's path, and the section unless it was the whole doc. */
fun crumbs(found: SectionFound): List<String> = crumbs(found.path, found.section.takeUnless { found.wholeDoc })

/**
 * The section a doc opened from [answer] should mark, when the doc is the one
 * the answer named (ProjectDocs.tsx `answeredIn`): none when it named the whole doc.
 */
fun focusSection(answer: DocAnswer?, docId: UUID): String? {
    val named = listOfNotNull(answer?.found, answer?.also).firstOrNull { it.docId == docId } ?: return null
    return named.section.takeUnless { named.wholeDoc }
}

/** What a question is sent as: trimmed, as the web's ask box trims it. Null when there is nothing to ask. */
fun questionToAsk(draft: String): String? = draft.trim().ifEmpty { null }

/** A timestamp as a day, "8 Sep", as the web's `formatStamp` draws it. The year is left off as noise. */
fun formatStamp(iso: String, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String = try {
    OffsetDateTime.parse(iso).atZoneSameInstant(zone).format(DateTimeFormatter.ofPattern("d MMM", locale))
} catch (_: DateTimeParseException) {
    iso
}

/** The tree's section label for [section] ("Engineering"), as the reader's breadcrumb starts. */
fun sectionLabel(tree: DocTree?, section: String): String =
    tree?.sections?.firstOrNull { it.section.value == section }?.label ?: section.replaceFirstChar { it.uppercase() }

/** The web page a doc is read on: what a link in it is relative to, and what Share sends. */
fun docPageUrl(server: String, projectKey: String, docId: UUID): String =
    "${docsPageUrl(server, projectKey)}?doc=$docId"

/** The web page an answer is on, `?ask=` the question, so it can be passed on as a link. */
fun answerPageUrl(server: String, projectKey: String, question: String): String =
    "${docsPageUrl(server, projectKey)}?ask=${URLEncoder.encode(question, "UTF-8").replace("+", "%20")}"

private fun docsPageUrl(server: String, projectKey: String) = "${server.trimEnd('/')}/p/$projectKey/docs"
