package cylist.app.today

import cylist.api.generated.models.FiledItem
import cylist.api.generated.models.PersonRead

/*
 * Tagging in prose: a person with `@`, a file with `>`, ported from the web
 * app's `components/mentions.ts`. A tag is the text somebody typed, recognised
 * when it is read against the people directory and the project's files; nothing
 * but the words is stored. The phone has to read a to-do written on the web the
 * way the web reads it, and write one the web reads back, so the rules here are
 * the TypeScript's one for one. The function each mirrors is named beside it.
 */

const val PERSON_SIGIL = '@'
const val FILE_SIGIL = '>'

/** What a name may not be glued to the front or back of (`WORD`). */
private val WORD = Regex("[\\p{L}\\p{N}_]")

/**
 * What a `>` may follow and still start a tag (`OPENS_A_FILE_TAG`): nothing, a
 * space, or something a phrase opens with, so `a->b` and `x >= 3` stay prose.
 * A predicate rather than the TypeScript's `/[\s([{"'`]/u`: Kotlin's
 * `isWhitespace` is Unicode whitespace, as `\s` is under the `u` flag, and it
 * needs no `(?U)`, which Android's ICU regex engine refuses.
 */
private fun opensAFileTag(char: Char): Boolean = char.isWhitespace() || char in "([{\"'`"

/** How far back [mentionQuery] looks for the sigil it is completing (`LONGEST_QUERY`). */
private const val LONGEST_QUERY = 80

/** How many files one `>` offers at once (`MOST_FILES_OFFERED`). */
private const val MOST_FILES_OFFERED = 12

/** A stretch of text: plain, a person tagged, or a file tagged (`MentionRun`). */
sealed interface TagRun {
    val text: String

    data class Plain(override val text: String) : TagRun

    data class Person(override val text: String, val person: PersonRead) : TagRun

    data class File(override val text: String, val file: FiledItem) : TagRun
}

/** A sigil under the caret, waiting for a name to be chosen for it (`MentionDraft`). */
data class TagDraft(val sigil: Char, val at: Int, val query: String)

private fun isWord(char: Char?): Boolean = char != null && WORD.matches(char.toString())

private fun canStartTag(sigil: Char, before: Char?): Boolean =
    if (sigil == FILE_SIGIL) before == null || opensAFileTag(before) else !isWord(before)

/**
 * Split text into its plain runs and its tags (`splitMentions`). Longest name
 * first, so `@Aditi K` is "Aditi K" and not "Aditi" with a stray "K"; a name
 * counts only when the text neither runs into it nor out of it.
 */
fun splitTags(text: String, people: List<PersonRead>, files: List<FiledItem> = emptyList()): List<TagRun> {
    if (PERSON_SIGIL !in text && FILE_SIGIL !in text) return if (text.isEmpty()) emptyList() else listOf(TagRun.Plain(text))

    val byName = people.sortedByDescending { it.name.length }
    val byFileName = files.sortedByDescending { it.name.length }
    val runs = mutableListOf<TagRun>()
    val plain = StringBuilder()
    var index = 0
    while (index < text.length) {
        val tag = tagAt(text, index, byName, byFileName)
        if (tag == null) {
            plain.append(text[index])
            index += 1
            continue
        }
        if (plain.isNotEmpty()) runs += TagRun.Plain(plain.toString())
        plain.clear()
        runs += tag
        index += tag.text.length
    }
    if (plain.isNotEmpty()) runs += TagRun.Plain(plain.toString())
    return runs
}

private fun tagAt(text: String, index: Int, people: List<PersonRead>, files: List<FiledItem>): TagRun? {
    val sigil = text[index]
    if (sigil != PERSON_SIGIL && sigil != FILE_SIGIL) return null
    if (!canStartTag(sigil, text.getOrNull(index - 1))) return null
    return if (sigil == PERSON_SIGIL) {
        wholeNameAt(text, index + 1, people) { it.name }?.let { TagRun.Person("$sigil${it.name}", it) }
    } else {
        wholeNameAt(text, index + 1, files) { it.name }?.let { TagRun.File("$sigil${it.name}", it) }
    }
}

/** The first candidate whose whole name sits at [from], ignoring case (`wholeNameAt`). */
private fun <T> wholeNameAt(text: String, from: Int, candidates: List<T>, name: (T) -> String): T? =
    candidates.firstOrNull { candidate ->
        val end = from + name(candidate).length
        end <= text.length &&
            text.substring(from, end).equals(name(candidate), ignoreCase = true) &&
            !isWord(text.getOrNull(end))
    }

/**
 * The tag the caret is inside, if it is inside one (`mentionQuery`). The query
 * may hold spaces, because names do. The nearest sigil wins, and one that
 * cannot start a tag where it sits (an address, an arrow) stops the search.
 */
fun tagQuery(value: String, caret: Int): TagDraft? {
    val earliest = maxOf(0, caret - LONGEST_QUERY)
    for (index in caret - 1 downTo earliest) {
        val char = value[index]
        if (char == '\n') return null
        if (char != PERSON_SIGIL && char != FILE_SIGIL) continue
        if (!canStartTag(char, value.getOrNull(index - 1))) return null
        return TagDraft(char, index, value.substring(index + 1, caret))
    }
    return null
}

/** Everyone whose name starts with the query; a bare `@` offers the lot (`matchingMembers`). */
fun matchingPeople(query: String, people: List<PersonRead>): List<PersonRead> =
    people.filter { it.name.lowercase().startsWith(query.lowercase()) }

/**
 * Files whose name holds the query anywhere, those starting with it first,
 * capped at twelve (`matchingFiles`).
 */
fun matchingFiles(query: String, files: List<FiledItem>): List<FiledItem> {
    val wanted = query.lowercase()
    val matches = files.filter { wanted in it.name.lowercase() }
    val (starts, rest) = matches.partition { it.name.lowercase().startsWith(wanted) }
    return (starts + rest).take(MOST_FILES_OFFERED)
}

/** Text after a name was chosen, and where the caret goes. */
data class Completed(val value: String, val caret: Int)

/**
 * Put a chosen name in place of what was typed towards it (`applyMention`),
 * followed by one space unless one is already there.
 */
fun applyTag(value: String, draft: TagDraft, name: String): Completed {
    val tail = value.substring(draft.at + 1 + draft.query.length)
    val head = value.substring(0, draft.at) + draft.sigil + name + if (tail.startsWith(" ")) "" else " "
    return Completed(head + tail, head.length)
}
