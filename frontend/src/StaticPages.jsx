import { siteConfig } from "./siteConfig";
import Navbar from "./Navbar";

const HOME_PATH = "/";

/**
 * Shared layout for the static legal/policy pages (terms, privacy,
 * cancellation). Renders the standard navbar, a titled content column, and a
 * back-to-home footer. Content is passed as children.
 */
function StaticPageLayout({ navigateTo, title, updated, children }) {
  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />
      <main className="static-page">
        <article className="static-page-content">
          <h1>{title}</h1>
          {updated ? <p className="static-page-updated">Last updated: {updated}</p> : null}
          {children}
        </article>
        <footer className="static-page-footer">
          <button type="button" onClick={() => navigateTo(HOME_PATH)}>← Back to home</button>
        </footer>
      </main>
    </>
  );
}

export function TermsAndConditions({ navigateTo }) {
  return (
    <StaticPageLayout navigateTo={navigateTo} title="Terms &amp; Conditions" updated="August 2026">
      <p>
        These terms and conditions govern your use of this website; by using this website, you
        accept these terms and conditions in full. If you disagree with these terms and conditions
        or any part of these terms and conditions, you must not use this website.
      </p>
      <p>
        This website uses cookies. By using this website and agreeing to these terms and
        conditions, you consent to WMF Japan's use of cookies in accordance with the terms of
        WMF Japan's privacy policy / cookies policy.
      </p>
      <section>
        <h2>License to use website</h2>
        <p>
          Unless otherwise stated, WMF Japan and/or its licensors own the intellectual property
          rights in the website and material on the website. Subject to the license below, all
          these intellectual property rights are reserved.
        </p>
        <p>
          You may view, download for caching purposes only, and print pages from the website for
          your own personal use, subject to the restrictions set out below and elsewhere in these
          terms and conditions.
        </p>
        <p>You must not:</p>
        <ul>
          <li>republish material from this website (including republication on another website);</li>
          <li>sell, rent or sub-license material from the website;</li>
          <li>show any material from the website in public;</li>
          <li>
            reproduce, duplicate, copy or otherwise exploit material on this website for a
            commercial purpose;
          </li>
          <li>edit or otherwise modify any material on the website.</li>
        </ul>
      </section>
      <section>
        <h2>Acceptable use</h2>
        <p>
          You must not use this website in any way that causes, or may cause, damage to the
          website or impairment of the availability or accessibility of the website; or in any way
          which is unlawful, illegal, fraudulent or harmful, or in connection with any unlawful,
          illegal, fraudulent or harmful purpose or activity.
        </p>
        <p>
          You must not use this website to copy, store, host, transmit, send, use, publish or
          distribute any material which consists of (or is linked to) any spyware, computer virus,
          Trojan horse, worm, keystroke logger, rootkit or other malicious computer software.
        </p>
        <p>
          You must not conduct any systematic or automated data collection activities (including
          without limitation scraping, data mining, data extraction and data harvesting) on or in
          relation to this website without WMF Japan's express written consent.
        </p>
        <p>You must not use this website to transmit or send unsolicited commercial communications.</p>
        <p>
          You must not use this website for any purposes related to marketing without
          WMF Japan's express written consent.
        </p>
      </section>
      <section>
        <h2>Restricted access</h2>
        <p>
          Access to certain areas of this website is restricted. WMF Japan reserves the right
          to restrict access to other areas of this website, or indeed this entire website, at
          WMF Japan's discretion.
        </p>
        <p>
          If WMF Japan provides you with a user ID and password to enable you to access
          restricted areas of this website or other content or services, you must ensure that the
          user ID and password are kept confidential.
        </p>
        <p>
          WMF Japan may disable your user ID and password in WMF Japan's sole discretion
          without notice or explanation.
        </p>
      </section>
      <section>
        <h2>User content</h2>
        <p>
          In these terms and conditions, "your user content" means material (including without
          limitation text, images, audio material, video material and audio-visual material) that
          you submit to this website, for whatever purpose.
        </p>
        <p>
          You grant to WMF Japan a worldwide, irrevocable, non-exclusive, royalty-free license
          to use, reproduce, adapt, publish, translate and distribute your user content in any
          existing or future media. You also grant to WMF Japan the right to sub-license these
          rights, and the right to bring an action for infringement of these rights.
        </p>
        <p>
          Your user content must not be illegal or unlawful, must not infringe any third party's
          legal rights, and must not be capable of giving rise to legal action whether against you
          or WMF Japan or a third party (in each case under any applicable law).
        </p>
        <p>
          You must not submit any user content to the website that is or has ever been the subject
          of any threatened or actual legal proceedings or other similar complaint.
        </p>
        <p>
          WMF Japan reserves the right to edit or remove any material submitted to this
          website, or stored on WMF Japan's servers, or hosted or published upon this website.
        </p>
      </section>
      <section>
        <h2>No warranties</h2>
        <p>
          This website is provided "as is" without any representations or warranties, express or
          implied. WMF Japan makes no representations or warranties in relation to this website
          or the information and materials provided on this website.
        </p>
        <p>
          Without prejudice to the generality of the foregoing paragraph, WMF Japan does not
          warrant that:
        </p>
        <ul>
          <li>this website will be constantly available, or available at all; or</li>
          <li>the information on this website is complete, true, accurate or non-misleading.</li>
        </ul>
        <p>Nothing on this website constitutes, or is meant to constitute, advice of any kind.</p>
      </section>
      <section>
        <h2>Limitations of liability</h2>
        <p>
          WMF Japan will not be liable to you (whether under the law of contact, the law of
          torts or otherwise) in relation to the contents of, or use of, or otherwise in
          connection with, this website:
        </p>
        <ul>
          <li>to the extent that the website is provided free-of-charge, for any direct loss;</li>
          <li>for any indirect, special or consequential loss; or</li>
          <li>
            for any business losses, loss of revenue, income, profits or anticipated savings, loss
            of contracts or business relationships, loss of reputation or goodwill, or loss or
            corruption of information or data.
          </li>
        </ul>
        <p>
          These limitations of liability apply even if WMF Japan has been expressly advised of
          the potential loss.
        </p>
      </section>
      <section>
        <h2>Exceptions</h2>
        <p>
          Nothing in this website disclaimer will exclude or limit any warranty implied by law that
          it would be unlawful to exclude or limit; and nothing in this website disclaimer will
          exclude or limit WMF Japan's liability in respect of any matter which it would be
          illegal or unlawful for WMF Japan to exclude or limit, or to attempt or purport to
          exclude or limit, its liability.
        </p>
      </section>
      <section>
        <h2>Reasonableness</h2>
        <p>
          By using this website, you agree that the exclusions and limitations of liability set
          out in this website disclaimer are reasonable.
        </p>
        <p>If you do not think they are reasonable, you must not use this website.</p>
      </section>
      <section>
        <h2>Other parties</h2>
        <p>
          You agree that the limitations of warranties and liability set out in this website
          disclaimer will protect WMF Japan's office bearers, employees, agents, subsidiaries,
          successors, assigns and sub-contractors as well as WMF Japan.
        </p>
      </section>
      <section>
        <h2>Unenforceable provisions</h2>
        <p>
          If any provision of this website disclaimer is, or is found to be, unenforceable under
          applicable law, that will not affect the enforceability of the other provisions of this
          website disclaimer.
        </p>
      </section>
      <section>
        <h2>Indemnity</h2>
        <p>
          You hereby indemnify WMF Japan and undertake to keep WMF Japan indemnified against
          any losses, damages, costs, liabilities and expenses (including without limitation legal
          expenses and any amounts paid by WMF Japan to a third party in settlement of a claim
          or dispute on the advice of WMF Japan's legal advisers) incurred or suffered by
          WMF Japan arising out of any breach by you of any provision of these terms and
          conditions.
        </p>
      </section>
      <section>
        <h2>Breaches of these terms and conditions</h2>
        <p>
          Without prejudice to WMF Japan's other rights under these terms and conditions, if
          you breach these terms and conditions in any way, WMF Japan may take such action as
          WMF Japan deems appropriate to deal with the breach, including suspending your access
          to the website, prohibiting you from accessing the website, blocking computers using your
          IP address from accessing the website, contacting your internet service provider to
          request that they block your access to the website and/or bringing court proceedings
          against you.
        </p>
      </section>
      <section>
        <h2>Variation</h2>
        <p>
          WMF Japan may revise these terms and conditions from time-to-time. Revised terms and
          conditions will apply to the use of this website from the date of the publication of the
          revised terms and conditions on this website. Please check this page regularly to ensure
          you are familiar with the current version.
        </p>
      </section>
      <section>
        <h2>Assignment</h2>
        <p>
          WMF Japan may transfer, sub-contract or otherwise deal with WMF Japan's rights
          and/or obligations under these terms and conditions without notifying you or obtaining
          your consent.
        </p>
        <p>
          You may not transfer, sub-contract or otherwise deal with your rights and/or obligations
          under these terms and conditions.
        </p>
      </section>
      <section>
        <h2>Severability</h2>
        <p>
          If a provision of these terms and conditions is determined by any court or other
          competent authority to be unlawful and/or unenforceable, the other provisions will
          continue in effect. If any unlawful and/or unenforceable provision would be lawful or
          enforceable if part of it were deleted, that part will be deemed to be deleted, and the
          rest of the provision will continue in effect.
        </p>
      </section>
      <section>
        <h2>Entire agreement</h2>
        <p>
          These terms and conditions constitute the entire agreement between you and WMF Japan
          in relation to your use of this website, and supersede all previous agreements in
          respect of your use of this website.
        </p>
        <p>
          BY USING THIS WEBSITE, YOU AGREE TO THESE TERMS AND CONDITIONS. IF YOU DO NOT AGREE TO
          THE TERMS AND CONDITIONS CONTAINED HEREIN, YOUR SOLE AND EXCLUSIVE REMEDY IS TO
          IMMEDIATELY DISCONTINUE USE OF THIS WEBSITE.
        </p>
      </section>
    </StaticPageLayout>
  );
}

export function PrivacyPolicy({ navigateTo }) {
  return (
    <StaticPageLayout navigateTo={navigateTo} title="Privacy Policy" updated="August 2026">
      <p>
        WMF Japan is committed to protecting your privacy. This Privacy Policy explains how
        your personal information is collected, used, and disclosed by WMF Japan.
      </p>
      <p>
        This Privacy Policy applies to our website,{" "}
        <a href={siteConfig.orgUrl}>{siteConfig.orgUrl}</a>, and its associated
        subdomains. By accessing or using our Service, you signify that you have read, understood,
        and agree to our collection, storage, use, and disclosure of your personal information as
        described in this Privacy Policy and our Terms of Service.
      </p>
      <section>
        <h2>Definitions and Key Terms</h2>
        <p>
          <strong>Service:</strong> refers to the service provided by WMF Japan as described in
          the relative terms (if available) and on this platform.
        </p>
        <p>
          <strong>Third-party service:</strong> refers to advertisers, contest sponsors,
          promotional and marketing partners, and others who provide our content or whose products
          or services we think may interest you.
        </p>
        <p>
          <strong>Website:</strong> WMF Japan's site, which can be accessed via this URL:{" "}
          <a href={siteConfig.orgUrl}>{siteConfig.orgUrl}</a>
        </p>
        <p>
          <strong>You:</strong> a person or entity that is registered with WMF Japan to use the
          Services.
        </p>
      </section>
      <section>
        <h2>What Information Do We Collect?</h2>
        <p>
          We collect information from you when you visit our website, register on our site,
          subscribe to our newsletter, respond to a survey or fill out a form.
        </p>
        <p>Phone Numbers · Email Addresses · Name / username · Addresses</p>
      </section>
      <section>
        <h2>How Do We Use the Information We Collect?</h2>
        <p>Any of the information we collect from you may be used in one of the following ways:</p>
        <ul>
          <li>
            To personalize your experience (your information helps us to better respond to your
            individual needs)
          </li>
          <li>
            To improve our website (we continually strive to improve our website offerings based on
            the information and feedback we receive from you)
          </li>
          <li>
            To improve website service (your information helps us to more effectively respond to
            your requests)
          </li>
          <li>To process transactions</li>
          <li>To administer a survey or other site feature</li>
          <li>To send periodic emails</li>
        </ul>
      </section>
      <section>
        <h2>How Do We Use Your Email Address?</h2>
        <p>
          By submitting your email address on this website, you agree to receive emails from us.
          You can cancel your participation in any of these email lists at any time by clicking on
          the opt-out link or other unsubscribe option that is included in the respective email. We
          only send emails to people who have authorized us to contact them, either directly, or
          through a third party. We do not send unsolicited commercial emails, because we hate spam
          as much as you do.
        </p>
      </section>
      <section>
        <h2>How Long Do We Keep Your Information?</h2>
        <p>
          We keep your information only so long as we need it to provide WMF Japan website
          services to you and fulfill the purposes described in this policy. This is also the case
          for anyone that we share your information with and who carries out services on our
          behalf. When we no longer need to use your information and there is no need for us to
          keep it to comply with our legal or regulatory obligations, we'll either remove it from
          our systems or depersonalize it so that we can't identify you.
        </p>
      </section>
      <section>
        <h2>How Do We Protect Your Information?</h2>
        <p>
          We implement a variety of security measures to maintain the safety of your personal
          information when you enter, submit, or access your personal information. We offer the use
          of a secure server. All supplied sensitive information is transmitted via Secure Socket
          Layer (SSL) technology. We cannot, however, ensure or warrant the absolute security of
          any information you transmit to WMF Japan or guarantee that your information on the
          Service may not be accessed, disclosed, altered, or destroyed by a breach of any of our
          physical, technical, or managerial safeguards.
        </p>
      </section>
      <section>
        <h2>Your Consent</h2>
        <p>
          By using our site, registering an account, or making a purchase, you consent to this
          Privacy Policy.
        </p>
      </section>
      <section>
        <h2>Links to Other Websites</h2>
        <p>
          This Privacy Policy applies only to the website services. The Services may contain links
          to other websites not operated or controlled by WMF Japan. We are not responsible for
          the content, accuracy or opinions expressed in such websites, and such websites are not
          investigated, monitored or checked for accuracy or completeness by us. Please remember
          that when you use a link to go from the Services to another website, our Privacy Policy
          is no longer in effect. Your browsing and interaction on any other website, including
          those that have a link on our platform, is subject to that website's own rules and
          policies. Such third parties may use their own cookies or other methods to collect
          information about you.
        </p>
      </section>
      <section>
        <h2>Cookies</h2>
        <p>
          WMF Japan uses "Cookies" to identify the areas of our website that you have visited.
          A Cookie is a small piece of data stored on your computer or mobile device by your web
          browser. We use Cookies to personalize the Content that you see on our website. Most web
          browsers can be set to disable the use of Cookies. However, if you disable Cookies, you
          may not be able to access functionality on our website correctly or at all. We never
          place Personally Identifiable Information in Cookies.
        </p>
      </section>
      <section>
        <h2>Kids' Privacy</h2>
        <p>
          We do not address anyone under the age of 13. We do not knowingly collect personally
          identifiable information from anyone under the age of 13. If You are a parent or guardian
          and You are aware that Your child has provided Us with Personal Data, please contact Us.
          If We become aware that We have collected Personal Data from anyone under the age of 13
          without verification of parental consent, We take steps to remove that information from
          Our servers.
        </p>
      </section>
      <section>
        <h2>Changes to Our Privacy Policy</h2>
        <p>
          If we decide to change our privacy policy, we will post those changes on this page,
          and/or update the Privacy Policy modification date below.
        </p>
      </section>
      <section>
        <h2>Third-Party Services</h2>
        <p>
          We may display, include or make available third-party content (including data,
          information, applications and other products services) or provide links to third-party
          websites or services ("Third-Party Services").
        </p>
        <p>
          You acknowledge and agree that WMF Japan shall not be responsible for any Third-Party
          Services, including their accuracy, completeness, timeliness, validity, copyright
          compliance, legality, decency, quality or any other aspect thereof. WMF Japan does not
          assume and shall not have any liability or responsibility to you or any other person or
          entity for any Third-Party Services.
        </p>
        <p>
          Third-Party Services and links thereto are provided solely as a convenience to you and
          you access and use them entirely at your own risk and subject to such third parties'
          terms and conditions.
        </p>
      </section>
      <section>
        <h2>Contact Us</h2>
        <p>Don't hesitate to contact us if you have any questions.</p>
        <p>
          Via Email:{" "}
          <a href={`mailto:${siteConfig.contactEmail}`}>{siteConfig.contactEmail}</a>
        </p>
        <p>
          Via this Link:{" "}
          <a href={`${siteConfig.orgUrl.replace(/\/+$/, "")}/en/home/contact`}>
            {`${siteConfig.orgUrl.replace(/\/+$/, "")}/en/home/contact`}
          </a>
        </p>
      </section>
    </StaticPageLayout>
  );
}

export function CancellationPolicy({ navigateTo }) {
  return (
    <StaticPageLayout navigateTo={navigateTo} title="Cancellation Policy" updated="October 2026">
      <section>
        <h2>Event Tickets</h2>
        <p>
          For cancellations and refunds of event tickets, please contact us. Please quote your registration ID and
          the email address used to register.
        </p>
      </section>
      <section>
        <h2>Important</h2>
        <p>
          Cancellation requests received <strong>after the applicable cancellation deadline</strong>{" "}
          will not be eligible for a refund.
        </p>
      </section>
    </StaticPageLayout>
  );
}
