package cylist.app.today

import cylist.api.generated.models.FiledItem
import cylist.api.generated.models.ItemKind
import cylist.api.generated.models.PersonRead
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** `mentions.test.ts`, case for case: the phone reads a tag the way the web does. */
class TagsTest {
    private val aditi = person("Aditi K")
    private val rohan = person("Rohan S")
    private val members = listOf(aditi, rohan)
    private val brief = file("brief.pdf", "Briefs")
    private val numbers = file("Q3 numbers.xlsx", "Finance inputs/2026")
    private val portal = file("Tax portal", "Finance inputs", ItemKind.link)
    private val files = listOf(brief, numbers, portal)

    /** Each run as `@Name`, `>name` or its bare text, so a test says which kind came back. */
    private fun runs(text: String, people: List<PersonRead> = members, filed: List<FiledItem> = files): List<String> =
        splitTags(text, people, filed).map {
            when (it) {
                is TagRun.Person -> "@${it.person.name}"
                is TagRun.File -> ">${it.file.name}"
                is TagRun.Plain -> it.text
            }
        }

    @Test
    fun findsPeople() {
        assertEquals(listOf("ping ", "@Aditi K", " about this"), runs("ping @Aditi K about this"))
        assertEquals(listOf("nothing to see"), runs("nothing to see"))
        assertEquals(emptyList<TagRun>(), splitTags("", members, files))
        assertEquals(listOf("@Aditi K"), runs("@Aditi K"))
        assertEquals(listOf("over to ", "@Rohan S"), runs("over to @Rohan S"))
        assertEquals(listOf("@Aditi K", " and ", "@Rohan S"), runs("@Aditi K and @Rohan S"))
        assertEquals(listOf("(", "@Aditi K", "), then"), runs("(@Aditi K), then"))
        assertEquals(listOf("first\n", "@Rohan S"), runs("first\n@Rohan S"))
    }

    @Test
    fun takesTheLongestNameAndOnlyAWholeOne() {
        assertEquals(listOf("@Aditi K"), runs("@Aditi K", listOf(person("Aditi"), aditi)))
        assertEquals(listOf("@Aditi Kumar"), runs("@Aditi Kumar"))
        assertEquals(listOf("@Nobody At All"), runs("@Nobody At All"))
    }

    @Test
    fun isNotFooledByAnAddressAndIgnoresCase() {
        assertEquals(listOf("mail deploy@Aditi K now"), runs("mail deploy@Aditi K now"))
        assertEquals(listOf("@Aditi K", " please"), runs("@aditi k please"))
    }

    @Test
    fun findsFilesBehindAGreaterThan() {
        assertEquals(listOf("see ", ">brief.pdf", " for the shape"), runs("see >brief.pdf for the shape"))
        assertEquals(listOf(">Q3 numbers.xlsx", " has the totals"), runs(">Q3 numbers.xlsx has the totals"))
        assertEquals(listOf("filed under ", ">Tax portal"), runs("filed under >Tax portal"))
        assertEquals(listOf("7 > 3, and >nothing.txt"), runs("7 > 3, and >nothing.txt"))
        assertEquals(listOf("(", ">brief.pdf", ")"), runs("(>brief.pdf)"))
        assertEquals(listOf("see\u00a0", ">brief.pdf"), runs("see\u00a0>brief.pdf"))
        assertEquals(listOf(">brief.pdf.sig"), runs(">brief.pdf.sig", members, listOf(brief, file("brief.pdf.sig", "Briefs"))))
        assertEquals(listOf(">brief.pdfx"), runs(">brief.pdfx"))
        assertEquals(listOf(">brief.pdf", " please"), runs(">BRIEF.PDF please"))
        assertEquals(listOf("@Aditi K", " sent ", ">brief.pdf"), runs("@Aditi K sent >brief.pdf"))
        assertEquals(listOf("see >brief.pdf"), runs("see >brief.pdf", members, emptyList()))
    }

    @Test
    fun leavesTheArrowsPeopleWriteInCodeAlone() {
        assertEquals(listOf("item->brief.pdf"), runs("item->brief.pdf"))
        assertEquals(listOf("() =>brief.pdf"), runs("() =>brief.pdf"))
        assertEquals(listOf("4>brief.pdf"), runs("4>brief.pdf"))
    }

    @Test
    fun namesTheFirstOfTwoFilesThatShareAName() {
        val mine = file("notes.md", "Mine")
        val theirs = file("notes.md", "Theirs")
        assertEquals(TagRun.File(">notes.md", mine), splitTags(">notes.md", members, listOf(mine, theirs)).single())
    }

    @Test
    fun seesTheTagUnderTheCaret() {
        assertEquals(TagDraft('@', 6, ""), tagQuery("hello @", 7))
        assertEquals(TagDraft('@', 6, "Aditi K"), tagQuery("hello @Aditi K", 14))
        assertNull(tagQuery("nothing here", 12))
        assertNull(tagQuery("@Aditi\nand then", 15))
        assertNull(tagQuery("deploy@example", 14))
        assertEquals(TagDraft('@', 13, "Ro"), tagQuery("@Aditi K and @Ro", 16))
        assertEquals(TagDraft('>', 4, "bri"), tagQuery("see >bri", 8))
        assertEquals(TagDraft('>', 14, "bri"), tagQuery("@Aditi K sent >bri", 18))
        assertNull(tagQuery("item->bri", 9))
    }

    @Test
    fun offersPeopleByTheStartOfTheirName() {
        assertEquals(members, matchingPeople("", members))
        assertEquals(listOf(aditi), matchingPeople("Ad", members))
        assertEquals(listOf(rohan), matchingPeople("rohan", members))
        assertEquals(emptyList<PersonRead>(), matchingPeople("Aditi Q", members))
    }

    @Test
    fun offersFilesByAnyPartOfTheirNameStartsFirst() {
        assertEquals(files, matchingFiles("", files))
        val dated = file("2026-01-atlas-brief.pdf", "Briefs")
        assertEquals(listOf(dated), matchingFiles("brief", listOf(dated)))
        assertEquals(listOf(brief, dated), matchingFiles("brief", listOf(dated, brief)))
        assertEquals(listOf(numbers), matchingFiles("q3 NUM", files))
        assertEquals(emptyList<FiledItem>(), matchingFiles("brief.pdf.sig", files))
        assertEquals(12, matchingFiles("note", (0 until 40).map { file("note-$it.md") }).size)
    }

    @Test
    fun completesANameAndLeavesTheCaretAfterIt() {
        assertEquals(Completed("ping @Aditi K ", 14), applyTag("ping @Ad", TagDraft('@', 5, "Ad"), "Aditi K"))
        assertEquals(Completed("see >brief.pdf ", 15), applyTag("see >bri", TagDraft('>', 4, "bri"), "brief.pdf"))
        assertEquals(Completed("ping @Aditi K about it", 13), applyTag("ping @Ad about it", TagDraft('@', 5, "Ad"), "Aditi K"))
    }
}
