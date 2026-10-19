package cylist.app.docs.markdown

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownTest {
    private fun parse(source: String) = MarkdownDoc.parse(source).blocks

    @Test
    fun headingsParagraphsAndInlineRuns() {
        val blocks = parse("# Title\n\nSome **bold**, *soft*, `code` and ~~gone~~ text.\n\n### Deeper")
        assertEquals(Block.Heading(0, 1, listOf(Inline.Plain("Title"))), blocks[0])
        val paragraph = blocks[1] as Block.Paragraph
        assertEquals(
            listOf(
                Inline.Plain("Some "),
                Inline.Strong(listOf(Inline.Plain("bold"))),
                Inline.Plain(", "),
                Inline.Emphasis(listOf(Inline.Plain("soft"))),
                Inline.Plain(", "),
                Inline.Code("code"),
                Inline.Plain(" and "),
                Inline.Strike(listOf(Inline.Plain("gone"))),
                Inline.Plain(" text."),
            ),
            paragraph.inlines,
        )
        assertEquals(3, (blocks[2] as Block.Heading).level)
    }

    @Test
    fun fencedAndIndentedCodeKeepTheirTextAndLanguage() {
        val blocks = parse("```bash\nmake setup   # deps\nmake dev\n```\n\n    indented\n")
        assertEquals(Block.Code(0, "bash", "make setup   # deps\nmake dev"), blocks[0])
        assertEquals(Block.Code(1, null, "indented"), blocks[1])
    }

    @Test
    fun listsAreBulletedOrNumberedFromTheirStartAndNest() {
        val blocks = parse("3. three\n4. four\n   - nested\n\n- [x] done\n- [ ] not yet\n")
        val ordered = blocks[0] as Block.ListBlock
        assertTrue(ordered.ordered)
        assertEquals(3, ordered.start)
        assertTrue(ordered.tight)
        val nested = ordered.items[1].blocks[1] as Block.ListBlock
        assertEquals(false, nested.ordered)
        assertEquals("nested", MarkdownDoc.blockText(nested))

        val tasks = blocks[1] as Block.ListBlock
        assertEquals(listOf(true, false), tasks.items.map { it.checked })
        assertEquals("done", MarkdownDoc.blockText(tasks.items[0]).trim())
    }

    @Test
    fun aLooseListIsNotTight() {
        val list = parse("- one\n\n- two\n")[0] as Block.ListBlock
        assertEquals(false, list.tight)
    }

    @Test
    fun tablesKeepHeaderRowsAndAlignment() {
        val table = parse("| Client | Default | Max |\n|---|:---:|---:|\n| Web | 50 | 200 |\n| `cli` | 20 |\n")[0] as Block.Table
        assertEquals(listOf(Align.Start, Align.Center, Align.End), table.alignments)
        assertEquals(listOf("Client", "Default", "Max"), table.header.map { MarkdownDoc.plainText(it) })
        assertEquals(2, table.rows.size)
        assertEquals(listOf(Inline.Code("cli")), table.rows[1][0])
    }

    @Test
    fun rawHtmlIsKeptAsTextNotMarkup() {
        val blocks = parse("<div>block</div>\n\nAn <b>inline</b> tag.")
        assertEquals(Block.Html(0, "<div>block</div>"), blocks[0])
        assertEquals("An <b>inline</b> tag.", MarkdownDoc.blockText(blocks[1]))
    }

    @Test
    fun linksAutolinksAndImages() {
        val inlines = (parse("[docs](https://cylist.in/p/ATL/docs) and https://example.com and ![a chart](chart.png)")[0] as Block.Paragraph).inlines
        assertEquals(Inline.Link("https://cylist.in/p/ATL/docs", listOf(Inline.Plain("docs"))), inlines[0])
        assertTrue(inlines.any { it is Inline.Link && it.url == "https://example.com" })
        assertEquals(Inline.Image("chart.png", "a chart"), inlines.last())
    }

    @Test
    fun quotesAndRules() {
        val blocks = parse("> quoted\n> more\n\n---\n")
        val quote = blocks[0] as Block.Quote
        assertEquals("quoted\nmore", MarkdownDoc.blockText(quote))
        assertTrue(blocks[1] is Block.Rule)
    }

    @Test
    fun footnotesAreNumberedByFirstReferenceAndGatheredAtTheFoot() {
        val blocks = parse("B[^b] then A[^a] then B again[^b].\n\n[^a]: Note A.\n[^b]: Note B.\n")
        val refs = (blocks[0] as Block.Paragraph).inlines.filterIsInstance<Inline.FootnoteRef>().map { it.number }
        assertEquals(listOf(1, 2, 1), refs)
        val notes = blocks.last() as Block.Footnotes
        assertEquals(listOf("Note B.", "Note A."), notes.notes.map { MarkdownDoc.blockText(it) })
    }

    @Test
    fun blockIdsAreUniqueAcrossTheTree() {
        val doc = MarkdownDoc.parse("# A\n\n- one\n  - two\n\n> quote\n\n| a |\n|---|\n| b |\n")
        fun ids(block: Block): List<Int> = listOf(block.id) + when (block) {
            is Block.ListBlock -> block.items.flatMap(::ids)
            is Block.Item -> block.blocks.flatMap(::ids)
            is Block.Quote -> block.blocks.flatMap(::ids)
            else -> emptyList()
        }
        val all = doc.blocks.flatMap(::ids)
        assertEquals(all.size, all.toSet().size)
    }

    @Test
    fun contentsAreTheLevelTwoHeadingsWithTheirPlace() {
        val doc = MarkdownDoc.parse("# Title\n\nIntro\n\n## The cursor\n\nText\n\n## Breaking `ties`\n\n### Not listed\n")
        assertEquals(listOf("The cursor" to 2, "Breaking ties" to 4), doc.contents)
    }

    // The section an answer points into: a port of markSection in DocReader.tsx.

    private val paging = MarkdownDoc.parse(
        """
        # Device list paging

        ## Index

        1. Breaking ties — how rows that share a key are ordered
        2. Page sizes — the defaults

        ## Breaking ties

        Rows are sorted by `updated_at` and then by `id`.

        ## Page sizes

        - A limit above the max is clamped, not refused.
        """.trimIndent(),
    )

    @Test
    fun aHeadingOfTheSectionsTitleWinsOverAnIndexEntryStartingWithIt() {
        val mark = paging.sectionMark("Breaking ties")
        val heading = paging.blocks[3] as Block.Heading
        assertEquals(SectionMark(heading.id, 3), mark)
    }

    @Test
    fun theTitleIsMatchedWithoutCaseOrATrailingEllipsis() {
        assertEquals(3, paging.sectionMark("BREAKING TIES…")?.topIndex)
    }

    @Test
    fun withoutAHeadingTheListItemOrParagraphItOpensWithIsMarked() {
        val mark = checkNotNull(paging.sectionMark("A limit above the max"))
        val list = paging.blocks[mark.topIndex] as Block.ListBlock
        assertEquals(list.items[0].id, mark.blockId)
    }

    @Test
    fun aSectionNotInTheDocMarksNothing() {
        assertNull(paging.sectionMark("Webhooks"))
        assertNull(paging.sectionMark("…"))
    }
}
