import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class ReleaseVersionTest {
    @Test
    fun `a tag becomes the version name without its v`() {
        assertEquals("0.3.2", ReleaseVersion.parse("v0.3.2").name)
        assertEquals("0.3.2", ReleaseVersion.parse("0.3.2").name)
        assertEquals("12.0.7", ReleaseVersion.parse(" v12.0.7\n").name)
    }

    @Test
    fun `the code packs major, minor and patch`() {
        assertEquals(3_002, ReleaseVersion.parse("v0.3.2").code)
        assertEquals(1_000_000, ReleaseVersion.parse("v1.0.0").code)
        assertEquals(2_099_999_999, ReleaseVersion.parse("v2099.999.999").code)
    }

    @Test
    fun `every later release has a higher code`() {
        val tags = listOf("v0.0.1", "v0.0.2", "v0.0.999", "v0.1.0", "v0.3.2", "v0.3.10", "v0.10.0", "v0.999.999", "v1.0.0", "v1.0.1", "v2.0.0")
        val codes = tags.map { ReleaseVersion.parse(it).code }
        assertTrue("codes $codes are not increasing", codes.zipWithNext().all { (a, b) -> a < b })
    }

    @Test
    fun `an unreleased build has the lowest code Android accepts`() {
        assertEquals("0.0.0", ReleaseVersion.UNRELEASED.name)
        assertEquals(1, ReleaseVersion.UNRELEASED.code)
    }

    @Test
    fun `what is not a release tag is refused`() {
        for (tag in listOf("", "v", "latest", "v1.2", "v1.2.3.4", "V1.2.3", "v01.2.3", "v1.2.3-rc1", "v1.2.3+build", "vv1.2.3", "v-1.2.3")) {
            assertThrows("\"$tag\"", IllegalArgumentException::class.java) { ReleaseVersion.parse(tag) }
        }
    }

    @Test
    fun `a version whose code could go backwards is refused`() {
        for (tag in listOf("v0.1000.0", "v0.0.1000", "v2100.0.0", "v99999999999.0.0")) {
            assertThrows("\"$tag\"", IllegalArgumentException::class.java) { ReleaseVersion.parse(tag) }
        }
    }
}
