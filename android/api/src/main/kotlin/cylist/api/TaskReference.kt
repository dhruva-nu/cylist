package cylist.api

/**
 * A task's human reference: `CYLIST-100`, or `CYLIST-100-2` for a sub-task.
 *
 * The API addresses tasks by this, not by id, so it is what a notification or a
 * deep link carries. The project key follows the backend's rule
 * (`_KEY_PATTERN` in backend/app/schemas/projects.py): a letter, then one to
 * five letters or digits. Keys compare case-insensitively, so they are held
 * upper case.
 */
data class TaskReference(val project: String, val number: Int, val subNumber: Int? = null) {
    override fun toString(): String =
        if (subNumber == null) "$project-$number" else "$project-$number-$subNumber"

    companion object {
        private val PATTERN = Regex("""([A-Za-z][A-Za-z0-9]{1,5})-([1-9][0-9]*)(?:-([1-9][0-9]*))?""")

        /** The reference [text] names, or null if it is not one. */
        fun parse(text: String): TaskReference? {
            val match = PATTERN.matchEntire(text.trim()) ?: return null
            val (project, number, sub) = match.destructured
            return TaskReference(
                project = project.uppercase(),
                number = number.toIntOrNull() ?: return null,
                subNumber = if (sub.isEmpty()) null else sub.toIntOrNull() ?: return null,
            )
        }
    }
}
