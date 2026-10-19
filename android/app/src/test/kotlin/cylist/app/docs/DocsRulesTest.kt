package cylist.app.docs

import cylist.api.generated.models.Alternative
import cylist.api.generated.models.DocAnswer
import cylist.api.generated.models.SectionFound
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.math.BigDecimal
import java.time.ZoneId
import java.util.Locale
import java.util.UUID

class DocsRulesTest {
    private val paging = UUID.fromString("01a150ad-a24c-70c2-8c75-72d6d8634cad")
    private val release = UUID.fromString("01a150ad-a26e-775e-8804-d52b9f69ed03")

    private fun found(doc: UUID = paging, section: String? = "Breaking ties", whole: Boolean = false, relevance: String? = "0.86") =
        SectionFound(doc, "Engineering / APIs / Device list paging", section, "Rows are sorted…", whole, relevance?.let(::BigDecimal))

    private fun alternative(relevance: String?, doc: UUID = release, section: String? = "Overview") =
        Alternative(doc, "Engineering / Mobile / Releasing the phone app", section, BigDecimal.ZERO, relevance?.let(::BigDecimal))

    private fun answer(
        status: DocAnswer.Status,
        found: SectionFound? = found(),
        also: SectionFound? = null,
        alternatives: List<Alternative> = emptyList(),
        reason: String? = null,
    ) = DocAnswer("How does paging break ties?", status, BigDecimal("0.7"), found, also, alternatives, reason)

    @Test
    fun everyStatusIsWordedAndTonedAsOnTheWeb() {
        val expected = mapOf(
            DocAnswer.Status.ok to ("Answered" to Tone.Ok),
            DocAnswer.Status.ambiguous to ("Answered — a close call" to Tone.Ok),
            DocAnswer.Status.unverified to ("Best guess only" to Tone.Hold),
            DocAnswer.Status.not_documented to ("Not in the docs" to Tone.Hold),
            DocAnswer.Status.unavailable to ("Could not ask" to Tone.Blocked),
        )
        DocAnswer.Status.entries.forEach { status ->
            assertEquals(expected.getValue(status), statusLabel(status) to statusTone(status))
        }
    }

    @Test
    fun onlyOkAndAmbiguousCountAsAnswered() {
        assertTrue(answered(answer(DocAnswer.Status.ok)))
        assertTrue(answered(answer(DocAnswer.Status.ambiguous)))
        assertFalse(answered(answer(DocAnswer.Status.unverified)))
        assertFalse(answered(answer(DocAnswer.Status.not_documented, found = null)))
    }

    @Test
    fun relevanceReadsAsAWholePercent() {
        assertEquals("86% relevant", percent(BigDecimal("0.86")))
        assertEquals("98% relevant", percent(BigDecimal("0.975")))
        assertNull(percent(null))
    }

    @Test
    fun besideAnAnswerOnlyNearMissesAreListed() {
        val answer = answer(
            DocAnswer.Status.ok,
            alternatives = listOf(alternative("0.61"), alternative("0.02"), alternative(null), alternative("0.3")),
        )
        assertEquals(listOf("0.61", "0.3"), weighed(answer).map { it.relevance.toString() })
        assertEquals("Also close", weighedHeading(answer))
    }

    @Test
    fun withoutAnAnswerTheBestGuessComesFirstAndEverythingWeighedIsListed() {
        val answer = answer(DocAnswer.Status.unverified, found = found(relevance = "0.4"), alternatives = listOf(alternative("0.02")))
        assertEquals(listOf(paging, release), weighed(answer).map { it.docId })
        assertEquals("Sections it weighed", weighedHeading(answer))
    }

    @Test
    fun aMissIsExplainedByItsStatus() {
        assertEquals(
            "The docs could not be asked just now: jev is not configured on this server..",
            missExplained(answer(DocAnswer.Status.unavailable, found = null, reason = "jev is not configured on this server.")),
        )
        assertEquals("The docs could not be asked just now: jev did not answer.", missExplained(answer(DocAnswer.Status.unavailable, found = null)))
        assertTrue(missExplained(answer(DocAnswer.Status.unverified)).startsWith("No section passed the relevance check"))
        assertTrue(missExplained(answer(DocAnswer.Status.not_documented, found = null)).startsWith("Nothing in the docs answers this yet."))
    }

    @Test
    fun routingIsShownUnlessTheReasonIsWhyJevWasNotAsked() {
        assertEquals("Routed by title", routing(answer(DocAnswer.Status.ok, reason = "Routed by title")))
        assertNull(routing(answer(DocAnswer.Status.unavailable, reason = "jev is down")))
        assertNull(routing(answer(DocAnswer.Status.ok)))
    }

    @Test
    fun crumbsAreThePathThenTheSectionUnlessItWasTheWholeDoc() {
        assertEquals(listOf("Engineering", "APIs", "Device list paging", "Breaking ties"), crumbs(found()))
        assertEquals(listOf("Engineering", "APIs", "Device list paging"), crumbs(found(whole = true)))
        assertEquals(listOf("Product", "Vault"), crumbs("Product / Vault", null))
    }

    @Test
    fun aDocOpenedFromAnAnswerMarksTheSectionTheAnswerNamedInIt() {
        val answer = answer(DocAnswer.Status.ok, also = found(doc = release, section = "On the day"))
        assertEquals("Breaking ties", focusSection(answer, paging))
        assertEquals("On the day", focusSection(answer, release))
        assertNull(focusSection(answer, UUID.randomUUID()))
        assertNull(focusSection(answer(DocAnswer.Status.ok, found = found(whole = true)), paging))
        assertNull(focusSection(null, paging))
    }

    @Test
    fun aQuestionIsTrimmedAndABlankOneIsNotAsked() {
        assertEquals("how do ties break?", questionToAsk("  how do ties break?\n"))
        assertNull(questionToAsk("   "))
    }

    @Test
    fun stampsReadAsADay() {
        assertEquals("14 Oct", formatStamp("2026-10-14T23:30:00Z", ZoneId.of("UTC"), Locale.UK))
        assertEquals("15 Oct", formatStamp("2026-10-14T23:30:00Z", ZoneId.of("Asia/Kolkata"), Locale.UK))
        assertEquals("not a date", formatStamp("not a date"))
    }

    @Test
    fun webLinksForADocAndAnAnswer() {
        assertEquals("https://cylist.in/p/ATL/docs?doc=$paging", docPageUrl("https://cylist.in/", "ATL", paging))
        assertEquals(
            "https://cylist.in/p/ATL/docs?ask=How%20do%20ties%20break%3F%20%26%20why",
            answerPageUrl("https://cylist.in", "ATL", "How do ties break? & why"),
        )
    }
}
