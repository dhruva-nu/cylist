/**
 * The app's version, taken from the repository's release tag (CYLIST-108).
 *
 * One tag, `vX.Y.Z`, versions the whole repository, the app included:
 * `versionName` is `X.Y.Z` and `versionCode` is `X * 1_000_000 + Y * 1_000 + Z`.
 * Android installs an APK over another only when its versionCode is not lower,
 * so the code has to grow with every release; packing the three numbers this
 * way keeps their order, as long as minor and patch stay below 1000. A tag that
 * cannot be packed is refused rather than given a code that might go backwards.
 */
data class ReleaseVersion(val major: Int, val minor: Int, val patch: Int) {
    init {
        require(major in 0..MAX_MAJOR) { "major $major is outside 0..$MAX_MAJOR" }
        require(minor in 0..999) { "minor $minor is outside 0..999" }
        require(patch in 0..999) { "patch $patch is outside 0..999" }
    }

    val name: String get() = "$major.$minor.$patch"

    /** Never below 1: Android refuses a versionCode of 0. */
    val code: Int get() = maxOf(1, major * 1_000_000 + minor * 1_000 + patch)

    companion object {
        /** The largest major whose code still fits Google Play's ceiling of 2_100_000_000. */
        const val MAX_MAJOR = 2_099

        /** What a build is when nothing names a release: a local or pull request build. */
        val UNRELEASED = ReleaseVersion(0, 0, 0)

        private val TAG = Regex("""v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)""")

        /**
         * Parses a release tag, `v0.3.2` or `0.3.2`. A pre-release or build
         * suffix (`v0.4.0-rc1`) is refused: it would share its code with the
         * release it precedes, and the release could not install over it.
         */
        fun parse(tag: String): ReleaseVersion {
            val match = TAG.matchEntire(tag.trim())
                ?: throw IllegalArgumentException("\"$tag\" is not a release tag like v1.2.3")
            val (major, minor, patch) = match.destructured
            return try {
                ReleaseVersion(major.toInt(), minor.toInt(), patch.toInt())
            } catch (e: NumberFormatException) {
                throw IllegalArgumentException("\"$tag\" has a number too large for a version", e)
            } catch (e: IllegalArgumentException) {
                throw IllegalArgumentException("\"$tag\" cannot be a versionCode: ${e.message}", e)
            }
        }
    }
}
